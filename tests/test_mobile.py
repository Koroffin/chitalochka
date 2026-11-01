import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from speedreader.library import LibraryStore
from speedreader.mobile import MobileLibraryPage

_app = None


def _quiet_offscreen(mode, context, message):
    # The offscreen platform warns on every show().
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


class MobileLibraryTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.page = MobileLibraryPage(self.store)
        self.page.resize(400, 700)
        self.addCleanup(self.page.deleteLater)

    def item(self, title):
        for i in range(self.page.list.count()):
            if title in self.page.list.item(i).text():
                return self.page.list.item(i)
        self.fail(title)

    def test_rows_and_header(self):
        self.store.set_position("k", 1, 0, flat=3)
        self.page.refresh()
        self.assertEqual(self.item("Первая").text(), "Первая — A — 50 %")
        self.assertEqual(self.page.add_button.text(), "Добавить")
        self.assertGreaterEqual(self.page.list.sizeHintForRow(0), 48)
        self.assertGreaterEqual(self.page.add_button.minimumHeight(), 48)

    def test_tap_opens_available_book_only(self):
        received = []
        self.page.open_requested.connect(lambda k, r: received.append((k, r)))
        self.page.list.itemClicked.emit(self.item("Первая"))
        self.assertEqual(received, [("k", False)])
        (self.data_dir / "books" / "k.json").unlink()
        self.page.refresh()
        self.assertIn("не найден", self.item("Первая").text())
        self.page.list.itemClicked.emit(self.item("Первая"))
        self.assertEqual(received, [("k", False)])

    def test_empty_library_shows_hint(self):
        self.store.remove("k")
        self.store.remove("k2")
        self.page.refresh()
        self.assertTrue(self.page.empty_label.isVisibleTo(self.page))
        self.assertEqual(self.page.empty_label.text(), "Библиотека пуста. Нажмите «Добавить».")
