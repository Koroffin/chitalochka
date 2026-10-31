import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from speedreader.idle import IdleInhibitor
from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import delay_for
from speedreader.ui.reader_page import ReaderPage
from speedreader.ui.ticker import HIGHLIGHT, TickerView

KEY = "loveread-1"
_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication([])


def make_book(blocks=None):
    if blocks is None:
        blocks = [
            {"type": "paragraph", "page": 1, "text": "Раз два три четыре пять. Шесть семь."},
            {"type": "heading", "page": 1, "text": "Глава вторая"},
            {"type": "paragraph", "page": 2, "text": "Восемь, «девять» — десять!"},
        ]
    return {
        "id": 1,
        "title": "T",
        "author": "A",
        "source": "https://loveread.ec/book-comments.php?book=1",
        "pages": 2,
        "pages_fetched": 2,
        "complete": True,
        "blocks": blocks,
    }


class FakeBackend:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1

    def release(self):
        self.released += 1


def highlight_width(ticker):
    # The first highlighted row is padding above the glyphs, so its width is the box width.
    image = ticker.grab().toImage()
    x = image.width() // 2
    y = next(y for y in range(image.height()) if image.pixelColor(x, y) == HIGHLIGHT)
    return sum(image.pixelColor(x, y) == HIGHLIGHT for x in range(image.width()))


class ReaderPageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LibraryStore(Path(tmp.name))
        self.book = make_book()
        self.store.add_book(KEY, self.book)
        self.backend = FakeBackend()
        self.page = ReaderPage(self.store, inhibitor=IdleInhibitor(self.backend))
        self.page.resize(900, 600)
        self.addCleanup(self.page.deleteLater)
        self.page.load(KEY, self.book)

    def test_ticker_shows_previous_current_next(self):
        words = self.page.model.words
        t = self.page.ticker
        self.assertEqual((t.previous, t.current, t.following), ("", words[0].text, words[1].text))
        self.page.model.set_position(1, 0)
        self.page.refresh()
        self.assertEqual((t.previous, t.current, t.following), ("семь. ¶", "Глава", "вторая ¶"))
        self.page.model.pos = len(words) - 1
        self.page.refresh()
        self.assertEqual((t.previous, t.current, t.following), (words[-2].text, words[-1].text, ""))

    def test_ticker_paints_highlight_in_the_middle(self):
        image = self.page.ticker.grab().toImage()
        self.assertFalse(image.isNull())
        y = image.height() // 2
        row = [image.pixelColor(x, y).name() for x in range(image.width())]
        self.assertIn("#ffe58a", row)
        self.assertNotEqual(row[0], "#ffe58a")

    def test_block_end_marked_and_heading_bold(self):
        t = self.page.ticker
        self.page.model.set_position(0, 6)
        self.page.refresh()
        self.assertEqual((t.previous, t.current, t.following), ("Шесть", "семь. ¶", "Глава"))
        self.assertEqual(t.bold, (False, False, True))
        self.page.model.set_position(1, 1)
        self.page.refresh()
        self.assertEqual((t.previous, t.current, t.following), ("Глава", "вторая ¶", "Восемь,"))
        self.assertEqual(t.bold, (True, True, False))
        self.page.model.pos = len(self.page.model.words) - 1
        self.page.refresh()
        self.assertEqual(t.current, "десять!")

    def test_bold_word_paints_wider_highlight(self):
        ticker = TickerView()
        ticker.resize(900, 200)
        self.addCleanup(ticker.deleteLater)
        ticker.set_words("", "Глава", "")
        regular = highlight_width(ticker)
        ticker.set_words("", "Глава", "", bold=(False, True, False))
        self.assertGreater(highlight_width(ticker), regular)

    def test_playback_keeps_computer_awake(self):
        self.page.show()
        self.page.play()
        self.assertEqual((self.backend.acquired, self.backend.released), (1, 0))
        self.page.pause()
        self.assertEqual((self.backend.acquired, self.backend.released), (1, 1))
        self.page.play()
        self.page.hide()
        self.assertEqual((self.backend.acquired, self.backend.released), (2, 2))
        self.page.show()
        self.page.play()
        self.page.load(KEY, self.book, restart=True)
        self.assertEqual((self.backend.acquired, self.backend.released), (3, 3))

    def test_play_advances_with_timer(self):
        self.store.wpm = 1000
        self.page.play()
        QTest.qWait(400)
        self.assertGreaterEqual(self.page.model.pos, 2)
        self.page.pause()
        block, word = self.page.model.position
        self.assertEqual(
            self.store.find(KEY)["position"],
            {"block": block, "word": word, "flat": self.page.model.pos},
        )

    def test_keys(self):
        QTest.keyClick(self.page, Qt.Key_Right)
        self.assertEqual(self.page.model.pos, 5)
        self.assertEqual(self.store.find(KEY)["position"], {"block": 0, "word": 5, "flat": 5})
        QTest.keyClick(self.page, Qt.Key_Space)
        self.assertTrue(self.page.playing)
        QTest.keyClick(self.page, Qt.Key_Space)
        self.assertFalse(self.page.playing)
        emitted = []
        self.page.back_requested.connect(lambda: emitted.append(True))
        QTest.keyClick(self.page, Qt.Key_Escape)
        self.assertEqual(emitted, [True])

    def test_keys_reach_page_from_ticker(self):
        QTest.keyClick(self.page.ticker, Qt.Key_Down)
        self.assertEqual(self.page.model.position, (1, 0))

    def test_slider_changes_store_wpm(self):
        self.page.wpm_slider.setValue(400)
        self.assertEqual(self.store.wpm, 400)
        m = self.page.model
        expected = round(delay_for(m.current, 400, m.weights(400)[m.pos]) * 1000)
        self.assertEqual(self.page.current_delay_ms(), expected)

    def test_click_on_ticker_toggles_play(self):
        QTest.mouseClick(self.page.ticker, Qt.LeftButton)
        self.assertTrue(self.page.playing)
        QTest.mouseClick(self.page.ticker, Qt.LeftButton)
        self.assertFalse(self.page.playing)
        self.assertEqual(self.store.find(KEY)["position"], {"block": 0, "word": 0, "flat": 0})

    def test_bookmark_added_and_listed(self):
        self.page.model.set_position(0, 5)
        self.page.add_bookmark()
        self.assertEqual(self.page.bookmarks_box.count(), 1)
        self.assertEqual(self.page.bookmarks_box.itemText(0), "Шесть семь.")
        self.assertEqual(len(self.store.find(KEY)["bookmarks"]), 1)
        self.page.add_bookmark()
        self.assertEqual(self.page.bookmarks_box.count(), 1)
        self.assertEqual(len(self.store.find(KEY)["bookmarks"]), 1)

    def test_emoji_words_shown_whole(self):
        book = make_book(
            [
                {"type": "paragraph", "page": 1, "text": "Альфа 😀 бета гамма."},
                {"type": "paragraph", "page": 1, "text": "😀😀 Дельта эпсилон."},
            ]
        )
        self.store.add_book("emoji", book)
        self.page.load("emoji", book)
        for i, word in enumerate(self.page.model.words):
            self.page.model.pos = i
            self.page.refresh()
            self.assertEqual(self.page.ticker.current.removesuffix(" ¶"), word.text)

    def test_block_without_type_loads(self):
        book = make_book([{"text": "Раз два."}, {"type": "heading", "text": "Три"}])
        self.store.add_book("notype", book)
        self.page.load("notype", book)
        self.assertEqual(self.page.model.word_count, 3)
        self.assertEqual(self.page.ticker.current, "Раз")

    def test_load_after_previous_book_removed(self):
        self.store.add_book("other", self.book)
        self.store.remove(KEY)
        self.page.load("other", self.book)
        self.assertEqual(self.page.key, "other")

    def test_large_book_builds_fast(self):
        text = " ".join(f"слово{i}" for i in range(60))
        book = make_book(
            [{"type": "paragraph", "page": 1, "text": text} for _ in range(1000)]
        )
        self.store.add_book("big", book)
        started = time.perf_counter()
        self.page.load("big", book)
        self.page.refresh()
        elapsed = time.perf_counter() - started
        self.assertEqual(self.page.model.word_count, 60000)
        self.assertLess(elapsed, 2.0)

    def test_out_of_range_wpm_in_file_reads_at_1000(self):
        path = Path(self.store._dir) / "library.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["wpm"] = 5000
        path.write_text(json.dumps(data), encoding="utf-8")
        store = LibraryStore(path.parent)
        page = ReaderPage(store)
        self.addCleanup(page.deleteLater)
        page.load(KEY, self.book)
        self.assertEqual(page.wpm_slider.value(), 1000)
        self.assertEqual(page.wpm_label.text(), "1000 сл/мин")
        m = page.model
        expected = round(delay_for(m.current, 1000, m.weights(1000)[m.pos]) * 1000)
        self.assertEqual(page.current_delay_ms(), expected)

    def failing_save(self):
        errors = []
        self.page.store_error.connect(errors.append)
        patcher = mock.patch.object(
            self.store, "save", side_effect=LibraryError("диск полон")
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return errors

    def test_pause_store_error_is_reported(self):
        errors = self.failing_save()
        QTest.keyClick(self.page, Qt.Key_Space)
        self.assertTrue(self.page.playing)
        QTest.keyClick(self.page, Qt.Key_Space)
        self.assertFalse(self.page.playing)
        self.assertEqual(len(errors), 1)
        self.assertIn("диск полон", errors[0])

    def test_slider_store_error_is_reported(self):
        errors = self.failing_save()
        self.page.wpm_slider.setValue(400)
        self.assertEqual(len(errors), 1)
        self.assertIn("диск полон", errors[0])
        self.assertEqual(self.store.wpm, 400)


if __name__ == "__main__":
    unittest.main()
