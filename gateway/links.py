"""Hardware/simulation serial link and length-prefixed TCP link with correlated ACKs."""
import json
import socket
import struct
import threading
import time

from shared.protocol import SlipDecoder, atr_encode, atr_decode, slip_encode

MAX_TCP_MESSAGE = 4096


def tcp_pack(message):
    body = json.dumps(message, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_TCP_MESSAGE:
        raise ValueError('TCP payload too large')
    return struct.pack('>I', len(body)) + body


class TCPDecoder:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        while len(self.buf) >= 4:
            size = struct.unpack('>I', self.buf[:4])[0]
            if size < 2 or size > MAX_TCP_MESSAGE:
                raise ValueError('TCP bad length prefix')
            if len(self.buf) < 4 + size:
                break
            msg = json.loads(bytes(self.buf[4:4 + size]).decode('utf-8'))
            del self.buf[:4 + size]
            if not isinstance(msg, dict):
                raise ValueError('TCP message not an object')
            yield msg


class Pending:
    def __init__(self):
        self.completed = threading.Event()
        self.received = set()
        self.last_message = {}
        self.final_message = None
        self.lock = threading.Lock()

    def update(self, msg):
        with self.lock:
            phase = msg.get('phase')
            self.received.add(phase)
            self.last_message = msg
            if (phase == 'COMPLETION' or phase == 'REJECTED') and self.final_message is None:
                self.final_message = msg
                self.completed.set()


class SerialLink:
    def __init__(self, serial_device, on_message, log):
        self.dev = serial_device
        self.on_message = on_message
        self.log = log
        self.out_seq = 0
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.last_seen = 0.0
        self.running = True
        self.thread = threading.Thread(target=self._read, daemon=True, name='serial-reader')
        self.thread.start()

    def send(self, obj):
        with self.send_lock:
            self.out_seq += 1
            frame = slip_encode(atr_encode(obj, self.out_seq))
            self.dev.write(frame)
        self.log('SERIAL', 'TX', obj.get('cmd_id', obj.get('job_id', '')), obj.get('phase', obj.get('type', '')), obj)

    def command(self, name, data=None, timeout=2.0, retries=3):
        cmd_id = 'A-' + str(time.monotonic_ns())
        request = {'type': 'COMMAND', 'command': name, 'cmd_id': cmd_id, **(data or {})}
        pending = Pending()
        with self.pending_lock:
            self.pending[cmd_id] = pending
        try:
            for _ in range(retries):
                self.send(request)
                if pending.completed.wait(timeout):
                    result = pending.final_message
                    if result.get('phase') == 'COMPLETION' and result.get('status') == 'OK':
                        return result
                    raise RuntimeError(f'ESP32 A rejected command: {result}')
            raise TimeoutError('No completion ACK for ESP32 A command: ' + name)
        finally:
            with self.pending_lock:
                self.pending.pop(cmd_id, None)

    def _read(self):
        decoder = SlipDecoder()
        while self.running:
            try:
                chunk = self.dev.read(256)
                if not chunk:
                    continue
                for raw in decoder.feed(chunk):
                    try:
                        _, seq, obj = atr_decode(raw)
                    except Exception as exc:
                        self.log('SERIAL', 'RX_ERROR', '', 'CRC_OR_FRAME', {'error': str(exc), 'hex': raw.hex()})
                        continue
                    self.last_seen = time.monotonic()
                    self.log('SERIAL', 'RX', obj.get('cmd_id', obj.get('job_id', '')), obj.get('phase', obj.get('type', '')), {'seq': seq, **obj, 'frame_hex': raw.hex()})
                    if obj.get('type') == 'ACK':
                        with self.pending_lock:
                            pend = self.pending.get(obj.get('ref_id', ''))
                        if pend is not None:
                            pend.update(obj)
                    else:
                        self.on_message(obj)
            except Exception as exc:
                self.log('SERIAL', 'ERROR', '', 'READ', {'error': str(exc)})
                time.sleep(0.5)

    def close(self):
        self.running = False
        try:
            self.dev.close()
        except Exception:
            pass


class TCPLink:
    def __init__(self, host, port, on_message, on_disconnect, log):
        self.host, self.port = host, port
        self.on_message, self.on_disconnect, self.log = on_message, on_disconnect, log
        self.sock = None
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.running = True
        self.connected = threading.Event()
        self.last_seen = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True, name='tcp-reader')
        self.thread.start()

    def _run(self):
        while self.running:
            try:
                s = socket.create_connection((self.host, self.port), timeout=2)
                s.settimeout(0.25)
                self.sock = s
                self.connected.set()
                self.last_seen = time.monotonic()
                decoder = TCPDecoder()
                self.log('TCP', 'INFO', '', 'CONNECTED', {'host': self.host, 'port': self.port})
                while self.running:
                    try:
                        data = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError('TCP peer closed')
                    for obj in decoder.feed(data):
                        self.last_seen = time.monotonic()
                        self.log('TCP', 'RX', obj.get('ref_id', obj.get('job_id', '')), obj.get('phase', obj.get('type', '')), obj)
                        if obj.get('type') == 'ACK':
                            with self.pending_lock:
                                pending = self.pending.get(obj.get('ref_id', ''))
                            if pending:
                                pending.update(obj)
                        else:
                            self.on_message(obj)
            except Exception as exc:
                self.log('TCP', 'INFO', '', 'DISCONNECTED', {'error': str(exc)})
            finally:
                was_connected = self.connected.is_set()
                self.connected.clear()
                self.sock = None
                try:
                    s.close()
                except Exception:
                    pass
                if was_connected:
                    self.on_disconnect()
            time.sleep(1.0)

    def send(self, obj):
        with self.send_lock:
            if not self.connected.is_set() or self.sock is None:
                raise ConnectionError('ESP32 B not connected')
            self.sock.sendall(tcp_pack(obj))
        self.log('TCP', 'TX', obj.get('cmd_id', obj.get('job_id', '')), obj.get('type', ''), obj)

    def request(self, obj, timeout=3.0, retries=3):
        ref = obj.get('job_id', obj.get('cmd_id'))
        if not ref:
            raise ValueError('request missing ID')
        pending = Pending()
        with self.pending_lock:
            self.pending[ref] = pending
        try:
            for attempt in range(retries):
                if not self.connected.wait(2.0):
                    continue
                try:
                    self.send(obj)
                except OSError:
                    continue
                if pending.completed.wait(timeout):
                    result = pending.final_message
                    if result.get('phase') == 'COMPLETION' and result.get('status') == 'OK':
                        return result
                    raise RuntimeError('ESP32 B rejected request: ' + str(result))
            raise TimeoutError(f'ESP32 B request timed out after {retries} attempts: {ref}')
        finally:
            with self.pending_lock:
                self.pending.pop(ref, None)

    def close(self):
        self.running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
