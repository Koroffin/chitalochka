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
from speedreader.mobile import (
    LONG_PRESS_MS,
    MobileLibraryPage,
    MobileReaderPage,
    classify_gesture,
)
from speedreader.reading import delay_for

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
        received = []
        self.page.open_requested.connect(lambda k, r: received.append((k, r)))
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
        self.assertEqual(received, [])

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


class MobileReaderTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.page = MobileReaderPage(self.store)
        self.page.resize(400, 700)
        self.addCleanup(self.page.deleteLater)
        self.book = self.store.load_book("k")
        self.page.load("k", self.book)

    def test_delay_is_200_wpm_regardless_of_store(self):
        self.store.wpm = 900
        m = self.page.model
        expected = round(delay_for(m.current, 200, m.weights(200)[m.pos]) * 1000)
        self.assertEqual(self.page.current_delay_ms(), expected)
        self.assertEqual(self.store.wpm, 900)

    def test_progress_strip_follows_position(self):
        self.assertEqual(self.page.strip.height(), 4)
        self.assertEqual(self.page.strip.fraction, 0.0)
        self.page.model.set_position(1, 0)
        self.page.refresh()
        self.assertAlmostEqual(self.page.strip.fraction, self.page.model.progress)

    def test_ticker_shows_words_with_paragraph_mark(self):
        self.page.model.set_position(0, 2)
        self.page.refresh()
        self.assertEqual(self.page.ticker.current, "три. ¶")

    def test_play_pause_saves_position(self):
        self.page.play()
        self.assertTrue(self.page.playing)
        QTest.qWait(50)
        self.page.pause()
        self.assertFalse(self.page.playing)
        pos = self.store.find("k")["position"]
        self.assertEqual(pos["flat"], self.page.model.pos)

    def test_rewind_while_paused_saves(self):
        self.page.model.set_position(1, 2)
        self.page.rewind()
        self.assertEqual(self.page.model.position, (1, 0))
        self.assertEqual(self.store.find("k")["position"]["flat"], 3)

    def test_classify_gesture(self):
        self.assertEqual(classify_gesture(5, 5, 400), "tap")
        self.assertEqual(classify_gesture(100, 10, 400), "rewind")
        self.assertEqual(classify_gesture(100, 60, 400), "none")
        self.assertEqual(classify_gesture(-100, 0, 400), "none")
        self.assertEqual(classify_gesture(0, 100, 400), "none")

    def swipe(self, dx, dy):
        start = QPoint(150, 300)
        QTest.mousePress(self.page.ticker, Qt.LeftButton, pos=start)
        QTest.mouseRelease(self.page.ticker, Qt.LeftButton, pos=start + QPoint(dx, dy))

    def test_tap_toggles_play(self):
        self.swipe(3, 2)
        self.assertTrue(self.page.playing)
        self.swipe(0, 0)
        self.assertFalse(self.page.playing)

    def test_swipe_right_rewinds_while_paused_and_saves(self):
        self.page.model.set_position(1, 1)
        self.page.refresh()
        self.swipe(120, 5)
        self.assertEqual(self.page.model.position, (1, 0))
        self.assertFalse(self.page.playing)
        self.assertEqual(self.store.find("k")["position"]["flat"], 3)

    def test_swipe_right_while_playing_keeps_playing(self):
        self.page.model.set_position(1, 1)
        self.page.play()
        self.swipe(120, 5)
        self.assertTrue(self.page.playing)
        self.assertEqual(self.page.model.position, (1, 0))

    def test_swipe_up_does_nothing(self):
        self.swipe(0, -150)
        self.assertFalse(self.page.playing)
        self.assertEqual(self.page.model.pos, 0)

    def test_tap_on_empty_book_does_nothing(self):
        self.store.add_book("empty", {**make_book(9, "Пустая"), "blocks": []})
        self.page.load("empty", self.store.load_book("empty"))
        self.swipe(0, 0)
        self.assertFalse(self.page.playing)
