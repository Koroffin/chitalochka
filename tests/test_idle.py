import sys
import unittest
from unittest import mock

from speedreader.idle import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    DBusBackend,
    IdleInhibitor,
    NullBackend,
    WindowsBackend,
    is_android,
    platform_backend,
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

    def test_is_android_follows_sys_attribute(self):
        with mock.patch.object(sys, "getandroidapilevel", create=True, new=lambda: 35):
            self.assertTrue(is_android())
        self.assertFalse(is_android())

    def test_platform_backend_is_null_on_android(self):
        with mock.patch.object(sys, "getandroidapilevel", create=True, new=lambda: 35):
            self.assertIsInstance(platform_backend(), NullBackend)


if __name__ == "__main__":
    unittest.main()
