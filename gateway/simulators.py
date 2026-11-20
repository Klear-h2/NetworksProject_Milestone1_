"""No-hardware simulators implementing the *same* serial/TCP wire protocols.
Only for development. Simulated logs are NOT hardware evidence for submission.
"""
import socket
import struct
import threading
import time

from gateway.links import TCPDecoder, tcp_pack
from shared.protocol import SlipDecoder, atr_decode, atr_encode, slip_encode


class SocketSerial:
    def __init__(self, sock):
        self.sock = sock
        self.sock.settimeout(0.1)

    def read(self, n):
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b''

    def write(self, data):
        self.sock.sendall(data)

    def close(self):
        self.sock.close()


class FakeProduction:
    def __init__(self, sock):
        self.sock = sock
        self.sock.settimeout(0.03)
        self.seq = 0
        self.configured_cycle = 500
        self.running = False
        self.paused = False
        self.target = 0
        self.batch_id = 0
        self.count = 0
        self.pending = None
        self.pending_msg = None
        self.last_send = 0
        self.retries = 0
        self.last_produced = 0
        self.last_heartbeat = 0
        self.commands = {}
        threading.Thread(target=self.run, daemon=True, name='fake-production').start()

    def send(self, obj):
        self.seq += 1
        try:
            self.sock.sendall(slip_encode(atr_encode(obj, self.seq)))
        except OSError:
            return

    def ack(self, ref, phase, status='OK'):
        self.send({'type': 'ACK', 'ref_id': ref, 'phase': phase, 'status': status})

    def handle(self, msg):
        if msg.get('type') == 'ACK':
            if self.pending and msg.get('ref_id') == self.pending:
                if msg.get('phase') == 'ACCEPTANCE':
                    self.pending = None
                    self.pending_msg = None
                    self.retries = 0
                elif msg.get('phase') == 'REJECTED':
                    self.running = False
                    self.pending = None
            return
        if msg.get('type') != 'COMMAND':
            return
        ref, cmd = msg.get('cmd_id'), msg.get('command')
        if not isinstance(ref, str):
            return
        self.ack(ref, 'DELIVERY')
        if ref in self.commands:
            self.ack(ref, 'ACCEPTANCE')
            self.ack(ref, 'COMPLETION', self.commands[ref])
            return
        valid = True
        if cmd == 'CONFIG':
            val = msg.get('production_ms')
            if type(val) is int and 100 <= val <= 30000:
                self.configured_cycle = val
            else:
                valid = False
        elif cmd == 'START':
            valid = type(msg.get('target')) is int and msg['target'] > 0 and type(msg.get('batch_id')) is int
            if valid:
                self.batch_id = msg['batch_id']
                self.target = msg['target']
                self.count = 0
                self.pending = None
                self.running = True
                self.paused = False
                self.last_produced = time.monotonic()
        elif cmd == 'PAUSE':
            self.paused = True
        elif cmd == 'RESUME':
            self.paused = False
            self.last_produced = time.monotonic()
        elif cmd == 'STOP':
            self.running = False
            self.paused = False
            self.pending = None
        else:
            valid = False
        status = 'OK' if valid else 'INVALID_COMMAND'
        self.commands[ref] = status
        if len(self.commands) > 100:
            self.commands.pop(next(iter(self.commands)))
        if valid:
            self.ack(ref, 'ACCEPTANCE')
            self.ack(ref, 'COMPLETION')
        else:
            self.ack(ref, 'REJECTED', status)

    def run(self):
        decoder = SlipDecoder()
        while True:
            try:
                chunk = self.sock.recv(512)
                if not chunk:
                    return
                for frame in decoder.feed(chunk):
                    try:
                        _, _, obj = atr_decode(frame)
                        self.handle(obj)
                    except ValueError:
                        continue
            except socket.timeout:
                pass
            except OSError:
                return
            now = time.monotonic()
            if now - self.last_heartbeat >= 2:
                self.last_heartbeat = now
                self.send({'type': 'HEARTBEAT', 'device': 'ESP32_A', 'heartbeat_id': self.seq + 1, **({'pending_job_id': self.pending} if self.pending else {})})
            if not self.running or self.paused:
                continue
            if self.pending:
                if now - self.last_send > 8:
                    if self.retries >= 3:
                        self.send({'type': 'FAULT', 'reason': 'PART acknowledgement timeout'})
                        self.running = False
                    else:
                        self.send(self.pending_msg)
                        self.retries += 1
                        self.last_send = now
            elif self.count < self.target and (now - self.last_produced) * 1000 >= self.configured_cycle:
                self.count += 1
                ref = f'B{self.batch_id}-P{self.count}'
                part = {'type': 'PART', 'job_id': ref, 'batch_id': self.batch_id, 'part_id': self.count}
                self.pending = ref
                self.pending_msg = part
                self.retries = 0
                self.last_send = self.last_produced = now
                self.send(part)


class FakeInspection:
    def __init__(self, host='127.0.0.1', port=5001):
        self.host, self.port = host, port
        self.n, self.k, self.ms = 4, 1, 1000
        self.batch_id, self.target = 0, 0
        self.done = {}
        self.commands = {}
        self.active = None
        self.active_end = 0
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(2)
        self.sock.settimeout(0.2)
        threading.Thread(target=self.run, daemon=True, name='fake-inspection').start()

    def run(self):
        while True:
            try:
                peer, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with peer:
                peer.settimeout(0.04)
                decoder = TCPDecoder()
                self.active = None
                last_heartbeat = 0
                while True:
                    try:
                        data = peer.recv(2048)
                        if not data:
                            break
                        for msg in decoder.feed(data):
                            self.handle(peer, msg)
                    except socket.timeout:
                        pass
                    except (OSError, ValueError):
                        break
                    now = time.monotonic()
                    if self.active and now >= self.active_end:
                        job = self.active
                        verdict = 'FAIL' if (job['part_id'] + job['k']) % job['n'] == 0 else 'PASS'
                        self.done[job['job_id']] = verdict
                        self.ack(peer, job['job_id'], 'COMPLETION', result=verdict)
                        self.active = None
                    if now - last_heartbeat >= 2:
                        last_heartbeat = now
                        self.send(peer, {'type': 'HEARTBEAT', 'device': 'ESP32_B', 'uptime_s': int(now)})

    @staticmethod
    def send(peer, msg):
        peer.sendall(tcp_pack(msg))

    def ack(self, peer, ref, phase, status='OK', result=None):
        message = {'type': 'ACK', 'ref_id': ref, 'phase': phase, 'status': status}
        if result is not None:
            message['result'] = result
        self.send(peer, message)

    def handle(self, peer, obj):
        kind = obj.get('type')
        ref = obj.get('job_id') if kind == 'INSPECT' else obj.get('cmd_id')
        if not isinstance(ref, str) or not ref or len(ref) > 64:
            return
        self.ack(peer, ref, 'DELIVERY')
        if kind == 'CONFIG':
            if ref in self.commands:
                self.ack(peer, ref, 'ACCEPTANCE')
                self.ack(peer, ref, 'COMPLETION')
                return
            if not (type(obj.get('inspection_ms')) is int and 100 <= obj['inspection_ms'] <= 30000
                    and type(obj.get('n')) is int and 1 <= obj['n'] <= 255
                    and type(obj.get('k')) is int and 0 <= obj['k'] <= 255):
                self.ack(peer, ref, 'REJECTED', 'INVALID_CONFIG')
                return
            self.ms, self.n, self.k = obj['inspection_ms'], obj['n'], obj['k']
            self.commands[ref] = True
            self.ack(peer, ref, 'ACCEPTANCE')
            self.ack(peer, ref, 'COMPLETION')
        elif kind == 'CANCEL_BATCH':
            if obj.get('batch_id') != self.batch_id:
                self.ack(peer, ref, 'REJECTED', 'WRONG_BATCH')
                return
            if self.active:
                self.ack(peer, self.active['job_id'], 'REJECTED', 'CANCELLED')
                self.active = None
            self.commands[ref] = True
            self.ack(peer, ref, 'ACCEPTANCE')
            self.ack(peer, ref, 'COMPLETION')
        elif kind == 'BEGIN_BATCH':
            new_batch, target = obj.get('batch_id'), obj.get('target')
            if type(new_batch) is not int or new_batch <= 0 or type(target) is not int or not 1 <= target <= 1000:
                self.ack(peer, ref, 'REJECTED', 'INVALID_BATCH')
                return
            self.batch_id, self.target = new_batch, target
            self.done.clear()
            self.active = None
            self.commands[ref] = True
            self.ack(peer, ref, 'ACCEPTANCE')
            self.ack(peer, ref, 'COMPLETION')
        elif kind == 'INSPECT':
            part = obj.get('part_id')
            batch = obj.get('batch_id')
            if (type(part) is not int or type(batch) is not int or part <= 0 or part > self.target
                    or batch != self.batch_id or ref != f'B{batch}-P{part}'):

                self.ack(peer, ref, 'REJECTED', 'INVALID_JOB')
                return
            if ref in self.done:
                self.ack(peer, ref, 'ACCEPTANCE')
                self.ack(peer, ref, 'COMPLETION', result=self.done[ref])
            elif self.active and self.active['job_id'] == ref:
                self.ack(peer, ref, 'ACCEPTANCE')
            elif self.active:
                self.ack(peer, ref, 'REJECTED', 'BUSY')
            else:
                self.active = {'job_id': ref, 'part_id': part, 'n': self.n, 'k': self.k}
                self.active_end = time.monotonic() + self.ms / 1000
                self.ack(peer, ref, 'ACCEPTANCE')
        else:
            self.ack(peer, ref, 'REJECTED', 'UNKNOWN_MESSAGE')
