import unittest

from speedreader.idle import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    DBusBackend,
    IdleInhibitor,
    WindowsBackend,
)


class FakeBackend:
    def __init__(self, fail=False):
        self.acquired = 0
        self.released = 0
        self.fail = fail

    def acquire(self):
        if self.fail:
            raise RuntimeError("no bus")
        self.acquired += 1

    def release(self):
        if self.fail:
            raise RuntimeError("no bus")
        self.released += 1


class IdleInhibitorTests(unittest.TestCase):
    def test_acquire_and_release_once(self):
        backend = FakeBackend()
        inhibitor = IdleInhibitor(backend)
        self.assertFalse(inhibitor.active)
        inhibitor.acquire()
        inhibitor.acquire()
        self.assertTrue(inhibitor.active)
        self.assertEqual(backend.acquired, 1)
        inhibitor.release()
        inhibitor.release()
        self.assertFalse(inhibitor.active)
        self.assertEqual(backend.released, 1)

    def test_backend_failure_does_not_raise(self):
        inhibitor = IdleInhibitor(FakeBackend(fail=True))
        inhibitor.acquire()
        self.assertTrue(inhibitor.active)
        inhibitor.release()
        self.assertFalse(inhibitor.active)

    def test_windows_backend_sets_thread_execution_state(self):
        calls = []
        backend = WindowsBackend(calls.append)
        backend.acquire()
        backend.release()
        self.assertEqual(
            calls, [ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED, ES_CONTINUOUS]
        )

    def test_dbus_backend_round_trip_does_not_raise(self):
        backend = DBusBackend()
        backend.acquire()
        backend.release()
        backend.acquire()
        backend.release()


if __name__ == "__main__":
    unittest.main()
