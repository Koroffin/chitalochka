import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import delay_for
from speedreader.ui.reader_page import ReaderPage

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


class ReaderPageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LibraryStore(Path(tmp.name))
        self.book = make_book()
        self.store.add_book(KEY, self.book)
        self.page = ReaderPage(self.store)
        self.page.resize(900, 600)
        self.addCleanup(self.page.deleteLater)
        self.page.load(KEY, self.book)

    def test_document_offsets_match_words(self):
        doc_text = self.page.text.document().toPlainText()
        words = self.page.model.words
        self.assertEqual(len(self.page.word_offsets), len(words))
        for off, w in zip(self.page.word_offsets, words):
            self.assertEqual(doc_text[off:off + len(w.text)], w.text)

    def test_highlight_follows_position(self):
        model = self.page.model
        model.set_position(1, 0)
        self.page.refresh()
        selections = self.page.text.extraSelections()
        self.assertEqual(len(selections), 1)
        cursor = selections[0].cursor
        off = self.page.word_offsets[model.pos]
        self.assertEqual(cursor.selectionStart(), off)
        self.assertEqual(cursor.selectionEnd(), off + len(model.current.text))

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

    def test_keys_reach_page_from_text_widget(self):
        QTest.keyClick(self.page.text, Qt.Key_Down)
        self.assertEqual(self.page.model.position, (1, 0))

    def test_slider_changes_store_wpm(self):
        self.page.wpm_slider.setValue(400)
        self.assertEqual(self.store.wpm, 400)
        expected = round(delay_for(self.page.model.current, 400) * 1000)
        self.assertEqual(self.page.current_delay_ms(), expected)

    def test_click_on_word_moves_position(self):
        self.page.jump_to_document_position(self.page.word_offsets[4] + 1)
        self.assertEqual(self.page.model.pos, 4)
        self.assertEqual(self.store.find(KEY)["position"], {"block": 0, "word": 4, "flat": 4})

    def test_bookmark_added_and_listed(self):
        self.page.model.set_position(0, 5)
        self.page.add_bookmark()
        self.assertEqual(self.page.bookmarks_box.count(), 1)
        self.assertEqual(self.page.bookmarks_box.itemText(0), "Шесть семь.")
        self.assertEqual(len(self.store.find(KEY)["bookmarks"]), 1)
        self.page.add_bookmark()
        self.assertEqual(self.page.bookmarks_box.count(), 1)
        self.assertEqual(len(self.store.find(KEY)["bookmarks"]), 1)

    def test_manual_scroll_pauses(self):
        self.page.play()
        self.assertTrue(self.page.playing)
        self.page.on_user_scrolled()
        self.assertFalse(self.page.playing)

    def test_offsets_count_utf16_units_after_emoji(self):
        book = make_book(
            [
                {"type": "paragraph", "page": 1, "text": "Альфа 😀 бета гамма."},
                {"type": "paragraph", "page": 1, "text": "😀😀 Дельта эпсилон."},
            ]
        )
        self.store.add_book("emoji", book)
        self.page.load("emoji", book)
        for i, word in enumerate(self.page.model.words):
            cursor = QTextCursor(self.page.text.document())
            cursor.setPosition(self.page.word_offsets[i])
            length = len(word.text.encode("utf-16-le")) // 2
            cursor.setPosition(self.page.word_offsets[i] + length, QTextCursor.KeepAnchor)
            self.assertEqual(cursor.selectedText(), word.text)
            self.page.model.pos = i
            self.page.refresh()
            selections = self.page.text.extraSelections()
            self.assertEqual(selections[0].cursor.selectedText(), word.text)

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
        expected = round(delay_for(page.model.current, 1000) * 1000)
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
