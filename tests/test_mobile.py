import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from speedreader.library import LibraryStore
from speedreader.mobile import LONG_PRESS_MS, MobileLibraryPage

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

    def test_menu_actions_depend_on_entry(self):
        self.assertEqual(self.page.menu_actions("k"), ["Начать заново", "Скачать заново", "Удалить"])
        (self.data_dir / "books" / "k.json").unlink()
        self.page.refresh()
        self.assertEqual(self.page.menu_actions("k"), ["Скачать заново", "Удалить"])
        book = make_book(3, "Импорт")
        book["source"] = ""
        self.store.add_book("import-3", book)
        self.page.refresh()
        self.assertEqual(self.page.menu_actions("import-3"), ["Начать заново", "Удалить"])

    def test_restart_resets_and_opens(self):
        self.store.set_position("k", 1, 2)
        received = []
        self.page.open_requested.connect(lambda k, r: received.append((k, r)))
        self.page.run_action("k", "Начать заново")
        self.assertEqual(received, [("k", True)])
        self.assertEqual(self.store.find("k")["position"], {"block": 0, "word": 0})

    def test_delete_asks(self):
        with mock.patch(
            "speedreader.mobile.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page.run_action("k", "Удалить")
        self.assertIsNone(self.store.find("k"))
        self.assertEqual(self.page.list.count(), 1)

    def test_long_press_opens_menu(self):
        self.page.show()
        shown = []
        with mock.patch.object(
            self.page, "show_menu", side_effect=lambda item, pos: shown.append(item.text())
        ):
            rect = self.page.list.visualItemRect(self.item("Первая"))
            QTest.mousePress(self.page.list.viewport(), Qt.LeftButton, pos=rect.center())
            self.assertTrue(self.page.long_press_timer.isActive())
            QTest.qWait(LONG_PRESS_MS + 100)
        self.assertEqual(len(shown), 1)
        self.assertIn("Первая", shown[0])
        QTest.mouseRelease(self.page.list.viewport(), Qt.LeftButton, pos=rect.center())

    def test_long_press_cancelled_by_move(self):
        self.page.show()
        rect = self.page.list.visualItemRect(self.item("Первая"))
        QTest.mousePress(self.page.list.viewport(), Qt.LeftButton, pos=rect.center())
        QTest.mouseMove(self.page.list.viewport(), rect.center() + QPoint(0, 30))
        self.assertFalse(self.page.long_press_timer.isActive())
        QTest.mouseRelease(
            self.page.list.viewport(), Qt.LeftButton, pos=rect.center() + QPoint(0, 30)
        )

    def test_release_before_timeout_cancels(self):
        self.page.show()
        rect = self.page.list.visualItemRect(self.item("Первая"))
        QTest.mousePress(self.page.list.viewport(), Qt.LeftButton, pos=rect.center())
        QTest.mouseRelease(self.page.list.viewport(), Qt.LeftButton, pos=rect.center())
        self.assertFalse(self.page.long_press_timer.isActive())
