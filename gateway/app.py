"""Run from repository root: python -m gateway.app --simulate
Hardware: python -m gateway.app --serial COM5 --inspection-host 192.168.137.XXX
"""
import argparse
import base64
import hashlib
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from gateway.controller import Factory

ROOT = Path(__file__).resolve().parent.parent


class WebSocketHub:
    def __init__(self, factory):
        self.factory = factory
        self.clients = set()
        self.lock = threading.Lock()
        threading.Thread(target=self.broadcast_loop, daemon=True).start()

    @staticmethod
    def frame(payload):
        data = payload.encode('utf-8')
        if len(data) < 126:
            return bytes([0x81, len(data)]) + data
        if len(data) <= 65535:
            return bytes([0x81, 126]) + len(data).to_bytes(2, 'big') + data
        return bytes([0x81, 127]) + len(data).to_bytes(8, 'big') + data

    def add(self, conn):
        with self.lock:
            self.clients.add(conn)

    def remove(self, conn):
        with self.lock:
            self.clients.discard(conn)

    def broadcast_loop(self):
        while True:
            time.sleep(0.5)
            frame = self.frame(json.dumps(self.factory.snapshot(), separators=(',', ':')))
            with self.lock:
                for conn in list(self.clients):
                    try:
                        conn.sendall(frame)
                    except OSError:
                        self.clients.discard(conn)


def make_handler(factory, hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'MCT431-Gateway/1.0'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            return  # CSV gateway log covers application events

        def json_reply(self, code, obj):
            body = json.dumps(obj, separators=(',', ':')).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/ws':
                return self.websocket()
            if path == '/api/status':
                return self.json_reply(200, factory.snapshot())
            files = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if path not in files:
                return self.json_reply(404, {'error': 'Not found'})
            filepath = ROOT / 'dashboard' / files[path]
            body = filepath.read_bytes()
            content_type = 'text/html' if path in ('/', '/index.html') else ('text/javascript' if path.endswith('.js') else 'text/css')
            self.send_response(200)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length > 4096:
                    return self.json_reply(413, {'error': 'Request too large'})
                raw = self.rfile.read(length)
                data = json.loads(raw.decode('utf-8')) if raw else {}
                fn = {'/api/start': factory.start, '/api/pause': factory.pause,
                      '/api/resume': factory.resume, '/api/stop': factory.stop}.get(path)
                if fn is None:
                    return self.json_reply(404, {'error': 'Unknown endpoint'})
                result = fn(data) if path == '/api/start' else fn()
                return self.json_reply(200, result)
            except (ValueError, RuntimeError, TimeoutError, ConnectionError) as exc:
                return self.json_reply(400, {'error': str(exc)})

        def websocket(self):
            key = self.headers.get('Sec-WebSocket-Key', '')
            if self.headers.get('Upgrade', '').lower() != 'websocket' or not key:
                return self.json_reply(400, {'error': 'WebSocket upgrade expected'})
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
            self.send_response(101, 'Switching Protocols')
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            hub.add(self.connection)
            self.close_connection = True
            try:
                self.connection.settimeout(2)
                while True:
                    try:
                        part = self.connection.recv(2)
                    except socket.timeout:
                        continue
                    if not part:
                        break
                    # Browser sends no application data. Any incoming frame is ignored.
            except OSError:
                pass
            finally:
                hub.remove(self.connection)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='MCT431 Milestone 1 gateway')
    parser.add_argument('--simulate', action='store_true', help='Run without ESP32s; no pip packages required')
    parser.add_argument('--serial', default='COM5', help='ESP32 A serial port on Windows (e.g. COM5)')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--inspection-host', default='192.168.137.100', help='ESP32 B IP address')
    parser.add_argument('--inspection-port', type=int, default=5001)
    parser.add_argument('--http-port', type=int, default=8000)
    parser.add_argument('--capacity', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.capacity <= 100:
        parser.error('--capacity must be from 1 to 100')
    host = args.inspection_host
    if args.simulate:
        from gateway.simulators import FakeInspection, FakeProduction, SocketSerial
        left, right = socket.socketpair()
        FakeProduction(right)
        host = '127.0.0.1'
        FakeInspection(host=host, port=args.inspection_port)
        serial_device = SocketSerial(left)
        evidence_dir = ROOT / 'evidence' / 'simulation'
        print('SIMULATION: fake ESP32 A and B; this is NOT physical-device evidence.')
    else:
        try:
            import serial  # PySerial only needed for physical ESP32 A
        except ImportError:
            sys.exit('PySerial missing. Run: py -m pip install pyserial')
        try:
            serial_device = serial.Serial(args.serial, baudrate=args.baud, timeout=0.1)
            time.sleep(1.5)  # board may reset when COM port opens
        except Exception as exc:
            sys.exit(f'Could not open {args.serial}: {exc}. Close ESP-IDF monitor first.')
        evidence_dir = ROOT / 'evidence' / 'hardware'
    factory = Factory(serial_device, host, args.inspection_port, evidence_dir, args.capacity)
    hub = WebSocketHub(factory)
    server = ThreadingHTTPServer(('0.0.0.0', args.http_port), make_handler(factory, hub))
    server.daemon_threads = True
    print(f'Gateway website on this PC: http://localhost:{args.http_port}')
    print(f'Other PC: http://<PC1-WIFI-IP>:{args.http_port}')
    print(f'ESP32 B TCP destination: {host}:{args.inspection_port}')
    print('Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        factory.serial.close()
        factory.tcp.close()


if __name__ == '__main__':
    main()
