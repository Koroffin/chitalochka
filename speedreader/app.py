import argparse
import importlib.resources
import os
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from speedreader.library import LibraryError, LibraryStore
from speedreader.ui import add_page as add_page_module
from speedreader.ui.add_page import AddPage
from speedreader.ui.library_page import LibraryPage
from speedreader.ui.reader_page import ReaderPage

_ERROR_TIMEOUT_MS = 10000


def default_data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if location:
        return Path(location)
    return Path.home() / ".local" / "share" / "SpeedReader"


def _icon_path() -> Path | None:
    path = importlib.resources.files("speedreader") / "assets" / "icon.png"
    return Path(str(path)) if path.is_file() else None


class MainWindow(QMainWindow):
    def __init__(self, store: LibraryStore):
        super().__init__()
        self.store = store
        self.setWindowTitle("SpeedReader")
        self.resize(1000, 700)
        icon = _icon_path()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))

        self.library_page = LibraryPage(store)
        self.add_page = AddPage(store)
        self.reader_page = ReaderPage(store)
        self.stack = QStackedWidget()
        for page in (self.library_page, self.add_page, self.reader_page):
            self.stack.addWidget(page)
        self.setCentralWidget(self.stack)

        self.library_page.open_requested.connect(self.open_book)
        self.library_page.add_requested.connect(self._show_add)
        self.library_page.redownload_requested.connect(self._redownload)
        self.library_page.store_error.connect(self._show_error)
        self.reader_page.back_requested.connect(self._back_from_reader)
        self.add_page.book_ready.connect(lambda key: self.open_book(key, False))
        self.add_page.back_requested.connect(self.show_library)
        self.show_library()

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Ошибка хранилища: {message}", _ERROR_TIMEOUT_MS)

    def show_library(self) -> None:
        self.library_page.refresh()
        try:
            # Hiding the reader saves its position.
            self.stack.setCurrentWidget(self.library_page)
        except LibraryError as e:
            self._show_error(str(e))
        self.library_page.list.setFocus()

    def open_book(self, key: str, restart: bool) -> None:
        try:
            book = self.store.load_book(key)
            self.reader_page.load(key, book, restart)
        except LibraryError as e:
            self._show_error(str(e))
            self.show_library()
            return
        self.stack.setCurrentWidget(self.reader_page)
        self.reader_page.setFocus()

    def _save_position(self) -> None:
        try:
            self.reader_page.save_position()
        except LibraryError as e:
            self._show_error(str(e))

    def _back_from_reader(self) -> None:
        self._save_position()
        self.show_library()

    def _show_add(self) -> None:
        self.add_page.error_label.clear()
        self.stack.setCurrentWidget(self.add_page)
        self.add_page.url_edit.setFocus()

    def _redownload(self, key: str) -> None:
        entry = self.store.find(key)
        if entry is None:
            return
        self.add_page.url_edit.setText(entry["source_url"])
        self.stack.setCurrentWidget(self.add_page)
        self.add_page.start_download(force=True)

    def closeEvent(self, event) -> None:
        self._save_position()
        self.add_page.shutdown()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="speedreader")
    parser.add_argument("--smoke", action="store_true", help="открыть окно и сразу выйти")
    parser.add_argument("--data-dir", type=Path, help="папка с библиотекой")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("SpeedReader")
    data_dir = args.data_dir or default_data_dir()
    try:
        store = LibraryStore(data_dir)
    except LibraryError as e:
        print(f"Ошибка хранилища: {e}", file=sys.stderr)
        return 1

    window = MainWindow(store)
    window.show()
    if args.smoke:
        QTimer.singleShot(0, app.quit)
    code = app.exec()
    if any(worker.isRunning() for worker in list(add_page_module._orphaned_workers)):
        # A running QThread destroyed at interpreter teardown aborts the process.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code
