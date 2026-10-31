import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_SERVICE = "org.freedesktop.ScreenSaver"
_PATH = "/org/freedesktop/ScreenSaver"
_CONNECTION = "speedreader-idle"


class WindowsBackend:
    def __init__(self, set_thread_execution_state):
        self._set = set_thread_execution_state

    def acquire(self) -> None:
        self._set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

    def release(self) -> None:
        self._set(ES_CONTINUOUS)


class DBusBackend:
    """Inhibits idle sleep through org.freedesktop.ScreenSaver (GNOME, KDE)."""

    def acquire(self) -> None:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        # The inhibit lives on its own connection: closing it releases the
        # inhibit, while UnInhibit needs a uint32 cookie PySide6 cannot send.
        bus = QDBusConnection.connectToBus(QDBusConnection.BusType.SessionBus, _CONNECTION)
        if bus.isConnected():
            QDBusInterface(_SERVICE, _PATH, _SERVICE, bus).call("Inhibit", "SpeedReader", "Чтение")

    def release(self) -> None:
        from PySide6.QtDBus import QDBusConnection

        QDBusConnection.disconnectFromBus(_CONNECTION)


class NullBackend:
    def acquire(self) -> None:
        pass

    def release(self) -> None:
        pass


def platform_backend():
    if sys.platform == "win32":
        import ctypes

        return WindowsBackend(ctypes.windll.kernel32.SetThreadExecutionState)
    if sys.platform.startswith("linux"):
        return DBusBackend()
    return NullBackend()


class IdleInhibitor:
    """Keeps the computer awake while reading runs, as a video player does."""

    def __init__(self, backend=None):
        self._backend = backend if backend is not None else platform_backend()
        self.active = False

    def acquire(self) -> None:
        if self.active:
            return
        self.active = True
        self._call(self._backend.acquire)

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        self._call(self._backend.release)

    @staticmethod
    def _call(method) -> None:
        try:
            method()
        except Exception:
            pass
