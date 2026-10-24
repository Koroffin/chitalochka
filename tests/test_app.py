import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMessageBox

from speedreader.app import MainWindow, default_data_dir, main
from speedreader.library import LibraryError, LibraryStore
from speedreader.ui.library_page import LibraryPage

_app = None


def _quiet_offscreen(mode, context, message):
    # The offscreen platform warns on every show(); main() must show the window.
    if message != "This plugin does not support propagateSizeHints()":
        print(message, file=sys.stderr)


def setUpModule():
    global _app
    qInstallMessageHandler(_quiet_offscreen)
    _app = QApplication.instance() or QApplication([])


def tearDownModule():
    qInstallMessageHandler(None)


def make_book(book_id=1, title="T"):
    return {
        "id": book_id,
        "title": title,
        "author": "A",
        "source": f"https://loveread.ec/book-comments.php?book={book_id}",
        "pages": 1,
        "pages_fetched": 1,
        "complete": True,
        "blocks": [
            {"type": "paragraph", "page": 1, "text": "Раз два три."},
            {"type": "paragraph", "page": 1, "text": "Четыре пять шесть."},
        ],
    }


class StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.store = LibraryStore(self.data_dir)
        self.store.add_book("k2", make_book(2, "Вторая"))
        self.store.add_book("k", make_book(1, "Первая"))


class LibraryPageTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.page = LibraryPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def row_text(self, key):
        for i in range(self.page.list.count()):
            if key in self.page.list.item(i).text():
                return self.page.list.item(i).text()
        self.fail(f"no row for {key}")

    def select(self, title):
        for i in range(self.page.list.count()):
            if title in self.page.list.item(i).text():
                self.page.list.setCurrentRow(i)
                return
        self.fail(f"no row for {title}")

    def test_library_lists_books_with_progress(self):
        self.store.set_position("k", 1, 0)
        self.page.refresh()
        self.assertEqual(self.page.list.count(), 2)
        self.assertEqual(self.row_text("Первая"), "Первая — A — 50 %")
        self.assertIn("0 %", self.row_text("Вторая"))

    def test_continue_and_restart_signals(self):
        self.store.set_position("k", 1, 2)
        self.page.refresh()
        received = []
        self.page.open_requested.connect(lambda key, restart: received.append((key, restart)))
        self.select("Первая")
        self.page.continue_button.click()
        self.assertEqual(received, [("k", False)])
        self.page.restart_button.click()
        self.assertEqual(received, [("k", False), ("k", True)])
        self.assertEqual(self.store.find("k")["position"], {"block": 0, "word": 0})

    def test_missing_file_disables_continue(self):
        (self.data_dir / "books" / "k.json").unlink()
        self.page.refresh()
        self.select("Первая")
        self.assertFalse(self.page.continue_button.isEnabled())
        self.assertTrue(self.page.redownload_button.isEnabled())
        self.assertIn("не найден", self.row_text("Первая"))

    def test_delete_asks_and_removes(self):
        self.select("Первая")
        with mock.patch(
            "speedreader.ui.library_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.page.delete_button.click()
        self.assertIsNotNone(self.store.find("k"))
        self.select("Первая")
        with mock.patch(
            "speedreader.ui.library_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page.delete_button.click()
        self.assertIsNone(self.store.find("k"))
        self.assertEqual(self.page.list.count(), 1)

    def test_malformed_book_file_marked_unavailable(self):
        (self.data_dir / "books" / "k.json").write_text("[]", encoding="utf-8")
        page = LibraryPage(self.store)
        self.addCleanup(page.deleteLater)
        self.page = page
        self.assertEqual(page.list.count(), 2)
        self.select("Первая")
        self.assertIn("не найден", self.row_text("Первая"))
        self.assertFalse(page.continue_button.isEnabled())
        self.assertTrue(page.redownload_button.isEnabled())


class MainWindowTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.store)
        self.addCleanup(self.window.deleteLater)

    def test_main_window_flow(self):
        w = self.window
        w.open_book("k", False)
        self.assertIs(w.stack.currentWidget(), w.reader_page)
        w.reader_page.model.set_position(1, 1)
        w.reader_page.back_requested.emit()
        self.assertIs(w.stack.currentWidget(), w.library_page)
        self.assertEqual(self.store.find("k")["position"], {"block": 1, "word": 1})
        w.add_page.book_ready.emit("k2")
        self.assertIs(w.stack.currentWidget(), w.reader_page)
        self.assertEqual(w.reader_page.model.position, (0, 0))

    def test_close_saves_position(self):
        self.window.open_book("k", False)
        self.window.reader_page.model.set_position(0, 2)
        self.window.close()
        self.assertEqual(self.store.find("k")["position"], {"block": 0, "word": 2})

    def test_store_error_goes_to_status_bar(self):
        w = self.window
        w.open_book("k", False)

        def fail():
            raise LibraryError("диск")

        with mock.patch.object(self.store, "save", side_effect=fail):
            w.reader_page.back_requested.emit()
        self.assertIn("диск", w.statusBar().currentMessage())
        self.assertIs(w.stack.currentWidget(), w.library_page)


    def test_open_book_store_error_goes_to_status_bar(self):
        w = self.window
        w.open_book("k", False)
        w.reader_page.back_requested.emit()
        with mock.patch.object(w.reader_page, "load", side_effect=LibraryError("диск")):
            w.open_book("k2", False)
        self.assertIn("диск", w.statusBar().currentMessage())
        self.assertIs(w.stack.currentWidget(), w.library_page)

    def test_restart_of_loaded_book_keeps_reset(self):
        w = self.window
        w.open_book("k", False)
        w.reader_page.model.set_position(1, 1)
        w.reader_page.save_position()
        w.reader_page.back_requested.emit()
        for i in range(w.library_page.list.count()):
            if "Первая" in w.library_page.list.item(i).text():
                w.library_page.list.setCurrentRow(i)
        w.library_page.restart_button.click()
        self.assertEqual(self.store.find("k")["position"], {"block": 0, "word": 0})
        self.assertEqual(w.reader_page.model.position, (0, 0))


class MainTest(unittest.TestCase):
    def test_default_data_dir_has_single_app_component(self):
        main(["--smoke", "--data-dir", tempfile.gettempdir()])
        path = default_data_dir()
        self.assertEqual(path.name, "SpeedReader")
        self.assertNotEqual(path.parent.name, "SpeedReader")

    def test_main_smoke_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["--smoke", "--data-dir", tmp]), 0)


if __name__ == "__main__":
    unittest.main()
