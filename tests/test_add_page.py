import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from speedreader.library import LibraryStore
from speedreader.sources import SourceError, book_key
from speedreader.ui import add_page
from speedreader.ui.add_page import AddPage

URL = "https://fake.test/book/1"
_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication([])


def make_book(book_id=1):
    return {
        "id": book_id,
        "title": "Фейк",
        "author": "Автор",
        "source": URL,
        "pages": 2,
        "pages_fetched": 2,
        "complete": True,
        "blocks": [{"type": "paragraph", "page": 1, "text": "Раз два три."}],
    }


class FakeSource:
    name = "fake.test"

    def __init__(self, error=None, wait_for_stop=False, stubborn=False):
        self.error = error
        self.wait_for_stop = wait_for_stop
        self.stubborn = stubborn
        self.calls = 0

    def matches(self, url):
        return "fake.test" in url

    def key_for_url(self, url):
        return "fake-1"

    def download(self, url, log, should_stop):
        self.calls += 1
        if self.stubborn:
            for _ in range(50):
                time.sleep(0.02)
            raise SourceError("Отменено")
        if self.wait_for_stop:
            deadline = time.monotonic() + 5
            while not should_stop() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise SourceError("Отменено")
        log("page 1/2: ok")
        if should_stop():
            raise SourceError("Отменено", pages_fetched=1)
        log("page 2/2: ok")
        if self.error:
            raise self.error
        return make_book()


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(20)
    return condition()


class AddPageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = LibraryStore(self.tmp / "lib")
        self.page = AddPage(self.store)
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.shutdown)
        self.ready = []
        self.page.book_ready.connect(self.ready.append)

    def use(self, source):
        self.page.sources = [source]
        self.page.url_edit.setText(URL)
        return source

    def test_unknown_url_shows_error_without_thread(self):
        self.page.url_edit.setText("https://example.com")
        self.page.start_download()
        self.assertIn("loveread.ec", self.page.error_label.text())
        self.assertFalse(self.page.busy)

    def test_download_adds_book_and_emits_key(self):
        self.use(FakeSource())
        self.page.start_download()
        self.assertTrue(wait_until(lambda: self.ready))
        self.assertEqual(self.ready, ["fake-1"])
        entry = self.store.find("fake-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["position"], {"block": 0, "word": 0})
        self.assertEqual(self.page.progress.value(), 2)
        self.assertFalse(self.page.busy)

    def test_existing_key_skips_download(self):
        source = self.use(FakeSource())
        self.store.add_book("fake-1", make_book())
        self.page.start_download()
        self.assertEqual(self.ready, ["fake-1"])
        self.assertEqual(source.calls, 0)
        self.assertFalse(self.page.busy)

    def test_force_downloads_existing_key(self):
        source = self.use(FakeSource())
        self.store.add_book("fake-1", make_book())
        self.page.start_download(force=True)
        self.assertTrue(wait_until(lambda: self.ready))
        self.assertEqual(source.calls, 1)

    def test_error_text_mentions_pages_fetched(self):
        self.use(FakeSource(error=SourceError("сеть", pages_fetched=3)))
        self.page.start_download()
        self.assertTrue(wait_until(lambda: not self.page.busy))
        self.assertIn("3", self.page.error_label.text())
        self.assertIn("сеть", self.page.error_label.text())
        self.assertEqual(self.store.entries, [])
        self.assertEqual(self.page.url_edit.text(), URL)
        self.assertEqual(self.ready, [])

    def test_cancel_unblocks_and_keeps_nothing(self):
        self.use(FakeSource(wait_for_stop=True))
        self.page.start_download()
        self.assertTrue(self.page.busy)
        self.page.request_cancel()
        self.assertEqual(self.page.cancel_button.text(), "Отмена…")
        self.assertFalse(self.page.cancel_button.isEnabled())
        self.assertTrue(wait_until(lambda: not self.page.busy))
        self.assertEqual(self.store.entries, [])
        self.assertEqual(self.page.cancel_button.text(), "Отмена")
        self.assertEqual(self.ready, [])

    def test_buttons_locked_while_busy(self):
        self.use(FakeSource(wait_for_stop=True))
        self.page.start_download()
        self.assertTrue(self.page.busy)
        for widget in (
            self.page.download_button,
            self.page.import_button,
            self.page.back_button,
            self.page.url_edit,
        ):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(self.page.cancel_button.isEnabled())
        self.assertFalse(self.page.cancel_button.isHidden())
        self.assertFalse(self.page.progress.isHidden())
        self.page.request_cancel()
        self.assertTrue(wait_until(lambda: not self.page.busy))
        self.assertTrue(self.page.download_button.isEnabled())
        self.assertTrue(self.page.cancel_button.isHidden())

    def test_shutdown_returns_true_when_worker_stops(self):
        self.assertTrue(self.page.shutdown())
        self.use(FakeSource(wait_for_stop=True))
        self.page.start_download()
        self.assertTrue(self.page.shutdown())
        self.assertFalse(self.page.busy)

    def test_shutdown_timeout_parks_running_worker(self):
        self.use(FakeSource(stubborn=True))
        self.page.start_download()
        worker = self.page._worker
        self.assertFalse(self.page.shutdown(100))
        self.assertFalse(self.page.busy)
        self.assertIn(worker, add_page._orphaned_workers)
        self.assertTrue(wait_until(lambda: worker not in add_page._orphaned_workers))
        self.assertTrue(worker.isFinished())

    def test_pdf_button_disabled(self):
        self.assertFalse(self.page.pdf_button.isEnabled())
        self.assertEqual(self.page.pdf_button.toolTip(), "Скоро: чтение из PDF")

    def test_import_json(self):
        book = make_book(7)
        good = self.tmp / "good.json"
        good.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
        self.page.import_json(str(good))
        self.assertEqual(self.ready, [book_key(book)])
        self.assertIsNotNone(self.store.find(book_key(book)))

        bad = self.tmp / "bad.json"
        bad.write_text("[1, 2]", encoding="utf-8")
        self.page.import_json(str(bad))
        self.assertNotEqual(self.page.error_label.text(), "")
        self.assertEqual(len(self.ready), 1)


if __name__ == "__main__":
    unittest.main()
