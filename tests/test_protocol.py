import unittest
from gateway.links import TCPDecoder, tcp_pack
from shared.protocol import atr_encode, atr_decode, crc16, SlipDecoder, slip_encode


class ProtocolTests(unittest.TestCase):
    def test_crc_known_value(self):
        self.assertEqual(crc16(b'123456789'), 0x29B1)

    def test_serial_round_trip_and_escape(self):
        obj = {'type': 'PART', 'job_id': 'B1-P1', 'special': 'a' + chr(192) + chr(219)}
        # JSON ascii escapes all non-ASCII, but ATR header/CRC may still contain END/ESC.
        original = atr_encode(obj, 219 * 192)
        frame = slip_encode(original)
        decoder = SlipDecoder()
        chunks = [frame[:3], frame[3:8], frame[8:]]
        packets = [packet for chunk in chunks for packet in decoder.feed(chunk)]
        self.assertEqual(len(packets), 1)
        self.assertEqual(atr_decode(packets[0])[2], obj)

    def test_serial_crc_rejects_corruption(self):
        packet = bytearray(atr_encode({'type': 'PART'}, 1))
        packet[8] ^= 0x04
        with self.assertRaisesRegex(ValueError, 'CRC'):
            atr_decode(packet)

    def test_slip_escaped_bytes(self):
        raw = bytes([0, 192, 219, 255])
        self.assertEqual(list(SlipDecoder().feed(slip_encode(raw))), [raw])

    def test_tcp_split_and_concatenated(self):
        first = {'type': 'INSPECT', 'job_id': 'B1-P1'}
        second = {'type': 'ACK', 'phase': 'COMPLETION', 'ref_id': 'B1-P1'}
        stream = tcp_pack(first) + tcp_pack(second)
        reader = TCPDecoder()
        results = []
        for i in range(0, len(stream), 3):
            results.extend(reader.feed(stream[i:i+3]))
        self.assertEqual(results, [first, second])

    def test_tcp_bad_length(self):
        with self.assertRaisesRegex(ValueError, 'length'):
            list(TCPDecoder().feed(b'\xff\xff\xff\xff'))


if __name__ == '__main__':
    unittest.main()
