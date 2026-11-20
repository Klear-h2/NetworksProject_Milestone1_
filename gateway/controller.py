"""Production state machine, bounded inspection queue, and safe behavior."""
import csv
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from gateway.links import SerialLink, TCPLink


class EventLogger:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = open(self.folder / 'events.csv', 'a', newline='', encoding='utf-8', buffering=1)
        self.serial_file = open(self.folder / 'serial_frames.log', 'a', encoding='utf-8', buffering=1)
        self.writer = csv.writer(self.file)
        if self.file.tell() == 0:
            self.writer.writerow(['utc_timestamp', 'monotonic_s', 'link', 'direction', 'identifier', 'event', 'details_json'])
        self.lock = threading.Lock()

    def __call__(self, link, direction, ident, event, details):
        timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        encoded = json.dumps(details, separators=(',', ':'), default=str)
        with self.lock:
            self.writer.writerow([timestamp, f'{time.monotonic():.6f}', link, direction, ident, event, encoded])
            if link == 'SERIAL':
                self.serial_file.write(f'{timestamp} {direction} {ident} {event} {encoded}\n')


class Factory:
    def __init__(self, serial_device, inspection_host, inspection_port, evidence_dir, queue_capacity=5):
        self.lock = threading.RLock()
        self.api_lock = threading.Lock()
        self.logger = EventLogger(evidence_dir)
        self.queue = deque()
        self.capacity = queue_capacity
        self.pending_part = None
        self.inflight = None
        self.jobs = {}
        self.events = deque(maxlen=70)
        self.state = 'Idle'
        self.fault_reason = None
        self.last_report = None
        self.target = 0
        self.produced = 0
        self.inspected = 0
        self.accepted = 0
        self.rejected = 0
        self.batch_id = 0
        self.settings = {'target': 10, 'production_ms': 500, 'inspection_ms': 1000, 'n': 4, 'k': 1}
        self.start_monotonic = 0
        self.serial = SerialLink(serial_device, self._on_serial, self.logger)
        self.tcp = TCPLink(inspection_host, inspection_port, self._on_tcp, self._tcp_disconnected, self.logger)
        threading.Thread(target=self._worker, daemon=True, name='inspection-worker').start()
        threading.Thread(target=self._watchdog, daemon=True, name='watchdog').start()

    def _note(self, event, **extra):
        self.events.appendleft({'time': datetime.now().strftime('%H:%M:%S'), 'event': event, **extra})
        self.logger('GATEWAY', 'EVENT', extra.get('job_id', ''), event, extra)

    def _fault(self, reason):
        with self.lock:
            if self.state not in ('Running', 'Paused'):
                return
            self.state = 'Fault'
            self.fault_reason = reason
            self._note('FAULT', reason=reason)
        try:
            self.serial.send({'type': 'COMMAND', 'command': 'STOP', 'cmd_id': 'A-fault-' + str(time.monotonic_ns())})
        except Exception:
            pass

    def _tcp_disconnected(self):
        self._fault('ESP32 B disconnected')

    def _on_tcp(self, obj):
        if obj.get('type') == 'HEARTBEAT':
            return
        with self.lock:
            self._note('TCP_EVENT', details=str(obj)[:160])

    def _serial_ack(self, job_id, phase, **kwargs):
        self.serial.send({'type': 'ACK', 'ref_id': job_id, 'phase': phase, 'status': 'OK', **kwargs})

    def _enqueue(self, job):
        # Called under self.lock; queue capacity never exceeded.
        self.queue.append(job)
        self.jobs[job['job_id']]['stage'] = 'QUEUED'
        self._serial_ack(job['job_id'], 'ACCEPTANCE')
        self._note('PART_QUEUED', job_id=job['job_id'], occupancy=len(self.queue))

    def _on_serial(self, obj):
        kind = obj.get('type')
        if kind == 'HEARTBEAT':
            # Re-acknowledge the outstanding part after a lost serial ACK, including
            # when backpressure previously blocked its acceptance.
            ref = obj.get('pending_job_id')
            if isinstance(ref, str):
                with self.lock:
                    record = self.jobs.get(ref)
                    if record is not None:
                        if record['stage'] == 'BLOCKED':
                            self._serial_ack(ref, 'BLOCKED')
                        else:
                            self._serial_ack(ref, 'ACCEPTANCE')
                            if record['stage'] == 'COMPLETED':
                                self._serial_ack(ref, 'COMPLETION', result=record['result'])
            return
        if kind == 'FAULT':
            self._fault('ESP32 A reported fault: ' + str(obj.get('reason', 'unknown')))
            return
        if kind != 'PART':
            return
        ref = obj.get('job_id')
        with self.lock:
            if not isinstance(ref, str) or len(ref) > 64:
                return
            self._serial_ack(ref, 'DELIVERY')
            if ref in self.jobs:
                record = self.jobs[ref]
                if record['stage'] == 'COMPLETED':
                    self._serial_ack(ref, 'ACCEPTANCE')
                    self._serial_ack(ref, 'COMPLETION', result=record['result'])
                elif record['stage'] == 'BLOCKED':
                    self._serial_ack(ref, 'BLOCKED')
                else:
                    self._serial_ack(ref, 'ACCEPTANCE')
                return
            part_id = obj.get('part_id')
            batch_id = obj.get('batch_id')
            if (self.state not in ('Running', 'Paused') or batch_id != self.batch_id
                    or type(part_id) is not int or not 1 <= part_id <= self.target
                    or ref != f'B{batch_id}-P{part_id}' or self.produced >= self.target):
                self.serial.send({'type': 'ACK', 'ref_id': ref, 'phase': 'REJECTED', 'status': 'INVALID_PART'})
                return
            self.jobs[ref] = {'job_id': ref, 'part_id': part_id, 'batch_id': batch_id, 'stage': 'RECEIVED'}
            self.produced += 1
            self._note('PART_PRODUCED', job_id=ref)
            if len(self.queue) < self.capacity:
                self._enqueue(self.jobs[ref])
            elif self.pending_part is None:
                self.pending_part = self.jobs[ref]
                self.jobs[ref]['stage'] = 'BLOCKED'
                self._serial_ack(ref, 'BLOCKED')
                self._note('QUEUE_FULL_BACKPRESSURE', job_id=ref)
            else:
                self._fault('More than one unaccepted part while the queue was full')

    def _accept_pending_if_space(self):
        # Called under self.lock.
        if self.pending_part and len(self.queue) < self.capacity:
            job = self.pending_part
            self.pending_part = None
            self._enqueue(job)
            self._note('BACKPRESSURE_RELEASED', job_id=job['job_id'])

    def _worker(self):
        while True:
            job = None
            with self.lock:
                if self.state in ('Running', 'Paused') and self.inflight is None and self.queue:
                    job = self.queue.popleft()
                    self.inflight = job
                    job['stage'] = 'INSPECTING'
                    self._accept_pending_if_space()
                    self._note('INSPECTION_SENT', job_id=job['job_id'])
            if job is None:
                time.sleep(0.025)
                continue
            with self.lock:
                duration_ms = self.settings['inspection_ms']
            try:
                result = self.tcp.request({'type': 'INSPECT', 'job_id': job['job_id'], 'part_id': job['part_id'], 'batch_id': job['batch_id']},
                                          timeout=max(3, duration_ms / 1000 + 2), retries=3)
                verdict = result.get('result')
                if verdict not in ('PASS', 'FAIL'):
                    raise ValueError(f'Invalid inspection verdict: {verdict}')
                with self.lock:
                    if job['batch_id'] == self.batch_id and self.state in ('Running', 'Paused'):
                        job['stage'] = 'COMPLETED'
                        job['result'] = verdict
                        self.inspected += 1
                        if verdict == 'PASS':
                            self.accepted += 1
                        else:
                            self.rejected += 1
                        self._serial_ack(job['job_id'], 'COMPLETION', result=verdict)
                        self._note('INSPECTION_COMPLETED', job_id=job['job_id'], result=verdict)
                        if self.produced == self.target and self.inspected == self.target:
                            self.state = 'Idle'
                            self.last_report = {'batch_id': self.batch_id, 'target': self.target,
                                                'produced': self.produced, 'inspected': self.inspected,
                                                'accepted': self.accepted, 'rejected': self.rejected,
                                                'status': 'COMPLETED'}
                            self._note('BATCH_COMPLETED', **self.last_report)
                    self.inflight = None
            except Exception as exc:
                with self.lock:
                    old_or_aborted = job['batch_id'] != self.batch_id or self.state == 'Idle'
                    self.inflight = None
                if not old_or_aborted:
                    self._fault('Inspection failure: ' + str(exc))

    def _watchdog(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                current = self.state
            if current in ('Running', 'Paused'):
                now = time.monotonic()
                if not self.serial.last_seen or now - self.serial.last_seen > 8:
                    self._fault('ESP32 A heartbeat missing for more than 8 seconds')
                if not self.tcp.connected.is_set() or now - self.tcp.last_seen > 8:
                    self._fault('ESP32 B heartbeat missing for more than 8 seconds')

    @staticmethod
    def _validate(options):
        limits = {'target': (1, 1000), 'production_ms': (100, 30000), 'inspection_ms': (100, 30000),
                  'n': (1, 255), 'k': (0, 255)}
        if not isinstance(options, dict):
            raise ValueError('Configuration must be an object')
        data = {}
        for key, (lo, hi) in limits.items():
            v = options.get(key)
            if type(v) is not int or not lo <= v <= hi:
                raise ValueError(f'{key} must be an integer between {lo} and {hi}')
            data[key] = v
        return data

    def start(self, configuration):
        config = self._validate(configuration)
        with self.api_lock:
            with self.lock:
                if self.state != 'Idle':
                    raise RuntimeError('Factory must be Idle to start; current state: ' + self.state)
                if self.inflight is not None:
                    raise RuntimeError('Previous inspection is still shutting down; retry Start after it clears')
                if not self.tcp.connected.is_set():
                    raise RuntimeError('ESP32 B is not connected. Check IP address, Wi-Fi, and TCP port.')
                if time.monotonic() - self.serial.last_seen > 8:
                    raise RuntimeError('ESP32 A is not sending heartbeats. Check COM port and cable.')
                self.settings = config
                self.batch_id += 1
                self.target = config['target']
                self.produced = self.inspected = self.accepted = self.rejected = 0
                self.queue.clear()
                self.pending_part = self.inflight = None
                self.jobs.clear()
                self.fault_reason = None
                self.last_report = None
                self.start_monotonic = time.monotonic()
                batch_id = self.batch_id
                self._note('CONFIGURING_BATCH', batch_id=batch_id)
            try:
                self.serial.command('CONFIG', {'production_ms': config['production_ms']})
                self.tcp.request({'type': 'CONFIG', 'cmd_id': 'B-config-' + str(time.monotonic_ns()),
                                  'inspection_ms': config['inspection_ms'], 'n': config['n'], 'k': config['k']})
                self.tcp.request({'type': 'BEGIN_BATCH', 'cmd_id': 'B-begin-' + str(time.monotonic_ns()),
                                  'batch_id': batch_id, 'target': config['target']})
                with self.lock:
                    self.state = 'Running'
                self.serial.command('START', {'batch_id': batch_id, 'target': config['target']})
                with self.lock:
                    self._note('BATCH_STARTED', batch_id=batch_id, settings=config)
                return self.snapshot()
            except Exception as exc:
                self._fault('Could not start batch: ' + str(exc))
                with self.lock:
                    if self.state != 'Fault':
                        self.state = 'Fault'
                        self.fault_reason = 'Could not start batch: ' + str(exc)
                raise

    def pause(self):
        with self.api_lock:
            with self.lock:
                if self.state != 'Running':
                    raise RuntimeError('Pause is only valid while Running')
            self.serial.command('PAUSE')
            with self.lock:
                self.state = 'Paused'
                self._note('BATCH_PAUSED')
            return self.snapshot()

    def resume(self):
        with self.api_lock:
            with self.lock:
                if self.state != 'Paused':
                    raise RuntimeError('Resume is only valid while Paused')
            self.serial.command('RESUME')
            with self.lock:
                self.state = 'Running'
                self._note('BATCH_RESUMED')
            return self.snapshot()

    def stop(self):
        with self.api_lock:
            with self.lock:
                if self.state == 'Idle':
                    return self.snapshot()
            try:
                self.serial.command('STOP')
            except Exception:
                pass
            with self.lock:
                self.last_report = {'status': 'ABORTED', 'batch_id': self.batch_id,
                                    'produced': self.produced, 'inspected': self.inspected,
                                    'accepted': self.accepted, 'rejected': self.rejected}
                self.state = 'Idle'
                self.queue.clear()
                self.pending_part = None
                self._note('BATCH_STOPPED')
                stopped_batch = self.batch_id
            # Request cancellation, if B is still online, to release an in-flight
            # job rather than waiting for its entire inspection time.
            if self.tcp.connected.is_set():
                try:
                    self.tcp.request({'type': 'CANCEL_BATCH', 'cmd_id': 'B-cancel-' + str(time.monotonic_ns()),
                                      'batch_id': stopped_batch}, timeout=1.5, retries=2)
                except Exception as exc:
                    self.logger('GATEWAY', 'INFO', '', 'CANCEL_FAILED', {'error': str(exc)})
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            return {
                'state': self.state, 'fault_reason': self.fault_reason, 'batch_id': self.batch_id,
                'settings': dict(self.settings), 'target': self.target, 'produced': self.produced,
                'inspected': self.inspected, 'accepted': self.accepted, 'rejected': self.rejected,
                'queue_occupancy': len(self.queue), 'queue_capacity': self.capacity,
                'backpressure': self.pending_part is not None,
                'inflight': None if self.inflight is None else self.inflight['job_id'],
                'esp32_a_online': bool(self.serial.last_seen and now - self.serial.last_seen < 8),
                'esp32_b_online': bool(self.tcp.connected.is_set() and now - self.tcp.last_seen < 8),
                'events': list(self.events)[:25], 'last_report': self.last_report,
            }
