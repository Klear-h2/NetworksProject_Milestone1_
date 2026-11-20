"""In-memory end-to-end tests; these do NOT constitute ESP32 hardware evidence."""
import socket
import tempfile
import time
import unittest
from gateway.controller import Factory
from gateway.simulators import FakeProduction, FakeInspection, SocketSerial


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        left, right = socket.socketpair()
        self.fake_a = FakeProduction(right)
        self.fake_b = FakeInspection(port=0)
        port = self.fake_b.sock.getsockname()[1]
        self.factory = Factory(SocketSerial(left), '127.0.0.1', port, self.temp.name, queue_capacity=2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.factory.snapshot()
            if status['esp32_a_online'] and status['esp32_b_online']:
                return
            time.sleep(.05)
        self.fail('Simulated devices did not connect')

    def tearDown(self):
        self.factory.serial.close()
        self.factory.tcp.close()
        self.fake_b.sock.close()
        self.fake_a.sock.close()
        self.temp.cleanup()

    def wait_for_completed(self, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.factory.snapshot()
            if status['fault_reason']:
                self.fail(status['fault_reason'])
            if status['last_report'] and status['last_report']['status'] == 'COMPLETED':
                return status
            time.sleep(0.05)
        self.fail('Batch did not complete in time')

    def test_batch_counts_and_same_job_ids(self):
        self.factory.start({'target': 10, 'production_ms': 100, 'inspection_ms': 130, 'n': 4, 'k': 1})
        status = self.wait_for_completed()
        self.assertEqual(status['produced'], 10)
        self.assertEqual(status['inspected'], 10)
        self.assertEqual(status['accepted'], 8)
        self.assertEqual(status['rejected'], 2)
        self.assertEqual(len(self.factory.jobs), 10)
        self.assertTrue(all(x['stage'] == 'COMPLETED' for x in self.factory.jobs.values()))
        # A retransmitted logical job with a new serial wire seq must not
        # increment produced or inspected counts again.
        self.factory._on_serial({'type': 'PART', 'job_id': 'B1-P3', 'batch_id': 1, 'part_id': 3})
        again = self.factory.snapshot()
        self.assertEqual((again['produced'], again['inspected'], again['accepted'], again['rejected']), (10, 10, 8, 2))

    def test_pause_resume_and_stop(self):
        self.factory.start({'target': 10, 'production_ms': 220, 'inspection_ms': 150, 'n': 4, 'k': 1})
        time.sleep(.5)
        self.factory.pause()
        produced_when_paused = self.factory.snapshot()['produced']
        time.sleep(.5)
        self.assertEqual(self.factory.snapshot()['produced'], produced_when_paused)
        self.factory.resume()
        final = self.wait_for_completed(timeout=10)
        self.assertEqual((final['accepted'], final['rejected']), (8, 2))
        self.factory.start({'target': 40, 'production_ms': 100, 'inspection_ms': 300, 'n': 5, 'k': 0})
        time.sleep(.4)
        self.factory.stop()
        self.assertEqual(self.factory.snapshot()['last_report']['status'], 'ABORTED')
        self.assertEqual(self.factory.snapshot()['state'], 'Idle')
        # Stop is allowed to return while an earlier inspection worker is winding
        # down. Wait for the old in-flight job to clear before starting a new batch.
        deadline = time.monotonic() + 4
        while self.factory.snapshot()['inflight'] is not None and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertIsNone(self.factory.snapshot()['inflight'])
        # Start again without restarting the gateway; old batch must not leak.
        self.factory.start({'target': 4, 'production_ms': 100, 'inspection_ms': 150, 'n': 2, 'k': 0})
        again = self.wait_for_completed(timeout=8)
        self.assertEqual((again['accepted'], again['rejected']), (2, 2))

    def test_queue_backpressure(self):
        self.factory.start({'target': 12, 'production_ms': 100, 'inspection_ms': 400, 'n': 3, 'k': 0})
        status = self.wait_for_completed()
        self.assertEqual((status['accepted'], status['rejected']), (8, 4))
        self.assertTrue(any(x['event'] == 'QUEUE_FULL_BACKPRESSURE' for x in self.factory.events))


if __name__ == '__main__':
    unittest.main()
