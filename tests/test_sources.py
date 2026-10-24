import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speedreader.sources import SOURCES, SourceError, book_key, detect, import_book_json
from speedreader.sources import loveread


class DetectTest(unittest.TestCase):
    def test_detect_variants(self):
        for url in (
            " https://loveread.ec/book-comments.php?book=36708#x ",
            "loveread.ec/read_book.php?id=36708&p=3",
            "http://www.loveread.ec/book-comments.php?book=36708",
        ):
            with self.subTest(url=url):
                source = detect(url)
                self.assertIsNotNone(source)
                self.assertEqual(source.key_for_url(url), "loveread-36708")

    def test_detect_unknown_returns_none(self):
        self.assertIsNone(detect("https://flibusta.is/b/1"))


class DownloadTest(unittest.TestCase):
    URL = "https://loveread.ec/book-comments.php?book=36708"

    def test_download_uses_fetcher_and_maps_errors(self):
        source = SOURCES[0]
        book = {"id": 36708, "title": "T", "blocks": []}
        log = lambda msg: None
        with mock.patch("speedreader.sources.loveread.download_book", return_value=book) as dl:
            self.assertIs(source.download(self.URL, log, lambda: False), book)
        dl.assert_called_once()
        self.assertEqual(dl.call_args.args[0], 36708)
        self.assertTrue(callable(dl.call_args.kwargs["fetcher"]))
        self.assertIs(dl.call_args.kwargs["log"], log)

        failing = mock.Mock(side_effect=loveread.LoveReadError("x", partial={"pages_fetched": 7}))
        with mock.patch("speedreader.sources.loveread.download_book", failing):
            with self.assertRaises(SourceError) as ctx:
                source.download(self.URL, log, lambda: False)
        self.assertEqual(ctx.exception.pages_fetched, 7)

        failing = mock.Mock(side_effect=loveread.LoveReadError("y"))
        with mock.patch("speedreader.sources.loveread.download_book", failing):
            with self.assertRaises(SourceError) as ctx:
                source.download(self.URL, log, lambda: False)
        self.assertEqual(ctx.exception.pages_fetched, 0)

    def test_should_stop_aborts_before_http(self):
        def no_http(url, timeout):
            raise AssertionError("HTTP must not be called")

        with mock.patch("speedreader.sources.loveread._urllib_opener", no_http):
            with self.assertRaises(SourceError):
                SOURCES[0].download(self.URL, lambda msg: None, lambda: True)


class ImportTest(unittest.TestCase):
    def write(self, text):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "b.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_import_json_valid_and_invalid(self):
        book = {"id": 5, "title": "Т", "blocks": [{"type": "paragraph", "text": "Раз два"}]}
        data = import_book_json(self.write(json.dumps(book, ensure_ascii=False)))
        self.assertEqual(data["title"], "Т")
        self.assertEqual(data["author"], "")
        with self.assertRaises(SourceError):
            import_book_json(self.write('{"id": 1}'))
        with self.assertRaises(SourceError):
            import_book_json(self.write("not json"))
        bad = dict(book, blocks=[{"type": "paragraph", "text": 3}])
        with self.assertRaises(SourceError):
            import_book_json(self.write(json.dumps(bad)))

    def test_book_key(self):
        self.assertEqual(book_key({"id": 7, "source": "https://loveread.ec/x?book=7"}), "loveread-7")
        self.assertEqual(book_key({"id": 7, "source": ""}), "import-7")
        self.assertEqual(book_key({"id": 7}), "import-7")

    def test_book_key_cannot_escape_books_dir(self):
        self.assertEqual(book_key({"id": "../x", "title": "T"}), "import-..x")
        self.assertEqual(book_key({"id": "a/b\\c d", "title": "T"}), "import-abcd")
        empty = book_key({"id": "/", "title": "T"})
        self.assertRegex(empty, r"^import-[0-9a-f]{12}$")
        self.assertEqual(empty, book_key({"id": "", "title": "T"}))

    def test_import_requires_string_title_and_drops_non_string_author(self):
        for title in (123, None, ["Т"], ""):
            book = {"id": 1, "title": title, "blocks": []}
            with self.subTest(title=title), self.assertRaises(SourceError):
                import_book_json(self.write(json.dumps(book)))
        book = {"id": 1, "title": "Т", "author": 5, "source": ["x"], "blocks": []}
        data = import_book_json(self.write(json.dumps(book)))
        self.assertEqual((data["author"], data["source"]), ("", ""))

    def test_import_rejects_id_of_wrong_type(self):
        for book_id in ([1], {"a": 1}, None, True):
            book = {"id": book_id, "title": "Т", "blocks": []}
            with self.subTest(book_id=book_id), self.assertRaises(SourceError):
                import_book_json(self.write(json.dumps(book)))


if __name__ == "__main__":
    unittest.main()
