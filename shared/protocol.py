"""MCT431 Milestone 1 framing. ATR header 8 bytes, CRC16 CCITT-FALSE, SLIP."""
import json
import struct

END, ESC, ESC_END, ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
MAX_ATR = 1600


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def atr_encode(obj: dict, seq: int, test_id: int = 1) -> bytes:
    body = json.dumps(obj, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    if len(body) > MAX_ATR - 10:
        raise ValueError('ATR payload too large')
    content = struct.pack('>HIH', test_id & 0xFFFF, seq & 0xFFFFFFFF, len(body)) + body
    return content + struct.pack('>H', crc16(content))


def atr_decode(raw: bytes) -> tuple[int, int, dict]:
    if len(raw) < 10 or len(raw) > MAX_ATR:
        raise ValueError('ATR invalid length')
    tid, seq, size = struct.unpack('>HIH', raw[:8])
    if len(raw) != 10 + size:
        raise ValueError('ATR payload_len does not match frame')
    expected = struct.unpack('>H', raw[-2:])[0]
    if crc16(raw[:-2]) != expected:
        raise ValueError('CRC mismatch')
    data = json.loads(raw[8:-2].decode('ascii'))
    if not isinstance(data, dict):
        raise ValueError('ATR payload is not a JSON object')
    return tid, seq, data


def slip_encode(raw: bytes) -> bytes:
    framed = bytearray([END])
    for byte in raw:
        if byte == END:
            framed.extend((ESC, ESC_END))
        elif byte == ESC:
            framed.extend((ESC, ESC_ESC))
        else:
            framed.append(byte)
    framed.append(END)
    return bytes(framed)


class SlipDecoder:
    def __init__(self, capacity=MAX_ATR):
        self.capacity = capacity
        self.data = bytearray()
        self.escape = False
        self.started = False

    def feed(self, chunk: bytes):
        for byte in chunk:
            if byte == END:
                if self.started and self.data and not self.escape:
                    yield bytes(self.data)
                self.data.clear()
                self.escape = False
                self.started = True
            elif not self.started:
                continue   # ignore UART bootloader text before the first SLIP END
            elif self.escape:
                if byte == ESC_END:
                    self.data.append(END)
                elif byte == ESC_ESC:
                    self.data.append(ESC)
                else:
                    self.data.clear()
                    self.started = False
                self.escape = False
            elif byte == ESC:
                self.escape = True
            elif len(self.data) >= self.capacity:
                self.data.clear()
                self.started = False
            else:
                self.data.append(byte)
