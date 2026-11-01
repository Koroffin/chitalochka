import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, Qt, qInstallMessageHandler
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from speedreader.library import LibraryError, LibraryStore

from speedreader.mobile import (
    LONG_PRESS_MS,
    MobileLibraryPage,
    MobileReaderPage,
    MobileWindow,
    classify_gesture,
    main,
    prepare_environment,
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

    def test_menu_items_are_touch_sized(self):
        styles = []

        class ClosedMenu(QMenu):
            def exec(self, *args):
                styles.append(self.styleSheet())

        with mock.patch("speedreader.mobile.QMenu", ClosedMenu):
            self.page.show_menu(self.item("Первая"), QPoint())
        self.assertEqual(styles, ["QMenu::item { min-height: 48px; }"])

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


class MobileWindowTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.window = MobileWindow(self.store)
        self.window.resize(400, 700)
        self.window.show()
        # With no event loop deleteLater never runs; a stale window would get the state signal.
        self.addCleanup(QApplication.sendPostedEvents, None, QEvent.DeferredDelete)
        self.addCleanup(self.window.deleteLater)

    def test_back_from_reader_saves_and_returns(self):
        self.window.open_book("k", False)
        self.assertIs(self.window.currentWidget(), self.window.reader_page)
        self.window.reader_page.model.set_position(1, 0)
        QTest.keyClick(self.window.reader_page, Qt.Key_Back)
        self.assertIs(self.window.currentWidget(), self.window.library_page)
        self.assertEqual(self.store.find("k")["position"]["flat"], 3)
        self.assertIn("50 %", self.window.library_page.item_text("k"))

    def test_back_sent_to_window_leaves_reader(self):
        self.window.open_book("k", False)
        QTest.keyClick(self.window, Qt.Key_Back)
        self.assertIs(self.window.currentWidget(), self.window.library_page)

    def test_back_from_add_returns_when_idle(self):
        self.window._show_add()
        QTest.keyClick(self.window.add_page, Qt.Key_Back)
        self.assertIs(self.window.currentWidget(), self.window.library_page)

    def test_back_from_add_ignored_while_busy(self):
        self.window._show_add()
        self.window.add_page._worker = object()
        self.addCleanup(setattr, self.window.add_page, "_worker", None)
        QTest.keyClick(self.window.add_page, Qt.Key_Back)
        self.assertIs(self.window.currentWidget(), self.window.add_page)

    def test_back_on_library_is_ignored(self):
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Back, Qt.NoModifier)
        QApplication.sendEvent(self.window, event)
        self.assertFalse(event.isAccepted())

    def test_background_pauses_reading(self):
        self.window.open_book("k", False)
        self.window.reader_page.play()
        QApplication.instance().applicationStateChanged.emit(Qt.ApplicationInactive)
        self.assertFalse(self.window.reader_page.playing)

    def test_background_on_library_keeps_store(self):
        self.window.open_book("k", False)
        self.window.reader_page.model.set_position(1, 0)
        self.window.back_from_reader()
        with mock.patch.object(self.store, "set_position") as set_position:
            QApplication.instance().applicationStateChanged.emit(Qt.ApplicationInactive)
        set_position.assert_not_called()

    def test_back_after_book_removed_does_not_raise(self):
        self.window.open_book("k", False)
        self.store.remove("k")
        QTest.keyClick(self.window.reader_page, Qt.Key_Back)
        self.assertIs(self.window.currentWidget(), self.window.library_page)

    def test_book_ready_opens_reader(self):
        self.window.add_page.book_ready.emit("k2")
        self.assertIs(self.window.currentWidget(), self.window.reader_page)
        self.assertEqual(self.window.reader_page.key, "k2")

    def test_open_book_storage_error_warns_and_shows_library(self):
        with mock.patch.object(QMessageBox, "warning") as warning:
            self.window.open_book("missing", False)
        warning.assert_called_once()
        self.assertIs(self.window.currentWidget(), self.window.library_page)


class EnvironmentTest(unittest.TestCase):
    def test_sets_cert_file_on_android_only(self):
        env = {}
        prepare_environment(env, android=True, cert_path=lambda: "/x/cacert.pem")
        self.assertEqual(env, {"SSL_CERT_FILE": "/x/cacert.pem"})
        env = {"SSL_CERT_FILE": "/keep"}
        prepare_environment(env, android=True, cert_path=lambda: "/x/cacert.pem")
        self.assertEqual(env["SSL_CERT_FILE"], "/keep")
        env = {}
        prepare_environment(env, android=False, cert_path=lambda: self.fail("imported certifi on desktop"))
        self.assertEqual(env, {})


class MainTest(unittest.TestCase):
    def run_main_with_unknown_argv(self, android: bool) -> int:
        with mock.patch("speedreader.mobile.is_android", return_value=android), \
                mock.patch.object(sys, "argv", ["speedreader", "--from-bootstrap"]), \
                mock.patch("speedreader.mobile.prepare_environment"), \
                mock.patch("speedreader.mobile.LibraryStore", side_effect=LibraryError("stop")), \
                mock.patch.object(QMessageBox, "critical"), \
                mock.patch("sys.stderr"):
            return main()

    def test_android_ignores_process_arguments(self):
        self.assertEqual(self.run_main_with_unknown_argv(android=True), 1)

    def test_desktop_rejects_unknown_arguments(self):
        with self.assertRaises(SystemExit):
            self.run_main_with_unknown_argv(android=False)

    def test_smoke_runs_and_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["--smoke", "--data-dir", tmp]), 0)
