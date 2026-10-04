import ctypes
import json
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, UTC

from benchmark.inspect_ai_v1.ev003_python_calendar import ScenarioCalendar


class CalendarTest(unittest.TestCase):
    def test_calendar_moves_but_elapsed_time_and_c_clock_do_not(self):
        libc = ctypes.CDLL(None)
        libc.time.argtypes = [ctypes.c_void_p]
        libc.time.restype = ctypes.c_long
        real_start = time.time()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clock.json'
            def publish(generation, offset):
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'contract': 'EvaScenarioCalendar/v1',
                    'generation': generation, 'offsetMilliseconds': offset}))
                temporary.replace(path)
            publish(0, -604800000)
            with ScenarioCalendar(path) as clock:
                before = time.monotonic()
                observed = datetime.now(UTC).timestamp()
                self.assertAlmostEqual(observed - libc.time(None), -604800, delta=1.1)
                time.sleep(.02)
                self.assertGreaterEqual(time.monotonic() - before, .015)
                self.assertGreater(datetime.now(UTC).timestamp(), observed)
                publish(1, 604800000)
                self.assertEqual(clock.refresh()['generation'], 1)
                self.assertAlmostEqual(datetime.now(UTC).timestamp() - libc.time(None), 604800, delta=1.1)
                self.assertLess(time.monotonic() - before, 2)
                self.assertAlmostEqual(time.clock_gettime(time.CLOCK_MONOTONIC), time.monotonic(), delta=.01)
                publish(1, 604801000)
                with self.assertRaises(ValueError): clock.refresh()
                publish(0, -604800000)
                with self.assertRaises(ValueError): clock.refresh()
                path.write_text('{}')
                with self.assertRaises(ValueError): clock.refresh()
            self.assertLess(abs(time.time() - libc.time(None)), 1.1)
            self.assertLess(time.time() - real_start, 2)


if __name__ == '__main__':
    unittest.main()
