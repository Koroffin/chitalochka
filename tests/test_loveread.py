import http.client
import json
import pathlib
import tempfile
import unittest
import urllib.error

from speedreader.sources import loveread

FIX = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def reader_page(current, total, body):
    nav = "".join(
        f'<span class="current">{p}</span>' if p == current
        else f'<a href="https://loveread.ec/read_book.php?id=7&p={p}" title="Страница - {p}">{p}</a>'
        for p in range(1, total + 1)
    )
    return (
        "<html><body>"
        f'<div class="navigation">{nav}</div>'
        f'<td class="read-book-text"><main id="readerContent" class="book-content">{body}</main></td>'
        f'<div class="navigation">{nav}</div>'
        "</body></html>"
    )


class ParseBookId(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(loveread.parse_book_id("36708"), 36708)

    def test_comments_url(self):
        self.assertEqual(loveread.parse_book_id("https://loveread.ec/book-comments.php?book=36708"), 36708)

    def test_read_url(self):
        self.assertEqual(loveread.parse_book_id("https://loveread.ec/read_book.php?id=36708&p=5"), 36708)

    def test_unicode_digits_rejected(self):
        with self.assertRaises(loveread.LoveReadError):
            loveread.parse_book_id("\u00b2\u00b3")

    def test_garbage_raises(self):
        with self.assertRaises(loveread.LoveReadError):
            loveread.parse_book_id("abc")


class ParseCommentsPage(unittest.TestCase):
    def test_metadata_and_read_link(self):
        info = loveread.parse_comments_page(fixture("comments_36708.html"))
        self.assertEqual(info["title"], "Выкрикивается лот 49")
        self.assertEqual(info["author"], "Томас Пинчон")
        self.assertEqual(info["read_url"], "https://loveread.ec/read_book.php?id=36708&p=1")

    def test_missing_book_redirected_to_index_raises(self):
        with self.assertRaises(loveread.LoveReadError):
            loveread.parse_comments_page(fixture("comments_missing_index.html"))


class ParseReadPage(unittest.TestCase):
    def test_first_page_structure(self):
        page = loveread.parse_read_page(fixture("read_36708_p1.html"))
        self.assertEqual(page["current"], 1)
        self.assertEqual(page["total"], 46)
        self.assertEqual(len(page["blocks"]), 18)
        self.assertEqual(page["blocks"][0], {"type": "paragraph", "text": "А. Жестокий порядок есть беспорядок; и"})

    def test_footnote_markers_removed(self):
        page = loveread.parse_read_page(fixture("read_36708_p1.html"))
        last = page["blocks"][-1]["text"]
        self.assertNotIn("[3]", last)
        self.assertTrue(last.endswith("проявлениях»."), last[-60:])

    def test_last_page_total_from_current(self):
        page = loveread.parse_read_page(fixture("read_36708_p46.html"))
        self.assertEqual(page["current"], 46)
        self.assertEqual(page["total"], 46)

    def test_chapter_headings(self):
        page = loveread.parse_read_page(fixture("read_1_p1.html"))
        headings = [b["text"] for b in page["blocks"] if b["type"] == "heading"]
        self.assertEqual(headings[:2], ["Пролог", "Глава первая О том, как Катю предали партизаны"])

    def test_br_becomes_newline_and_entities_decoded(self):
        html = reader_page(1, 1, "<p class=MsoNormal>Раз<br>Два &amp; три</p>")
        page = loveread.parse_read_page(html)
        self.assertEqual(page["blocks"], [{"type": "paragraph", "text": "Раз\nДва & три"}])

    def test_empty_paragraphs_skipped(self):
        html = reader_page(1, 1, "<p class=MsoNormal> </p><p class=MsoNormal>Текст</p><p></p>")
        self.assertEqual([b["text"] for b in loveread.parse_read_page(html)["blocks"]], ["Текст"])

    def test_nested_block_counted_once(self):
        html = reader_page(1, 1, '<div class="book-title"><p>Глава 2</p></div><p>текст</p>')
        self.assertEqual(loveread.parse_read_page(html)["blocks"],
                         [{"type": "heading", "text": "Глава 2"}, {"type": "paragraph", "text": "текст"}])

    def test_source_newline_inside_paragraph_is_a_space(self):
        html = reader_page(1, 1, '<p class=MsoNormal>курс литературы\n<a href="notes.php?id=1#1">[1]</a>\n). Прервав\n   учебу</p>')
        self.assertEqual(loveread.parse_read_page(html)["blocks"][0]["text"], "курс литературы ). Прервав учебу")

    def test_unclosed_paragraph_is_closed_by_next_block(self):
        html = reader_page(1, 1, '<p class=MsoNormal>первый<p class=MsoNormal>второй</p><div class="book-title">Глава 2</div><p>третий</p>')
        self.assertEqual([(b["type"], b["text"]) for b in loveread.parse_read_page(html)["blocks"]],
                         [("paragraph", "первый"), ("paragraph", "второй"), ("heading", "Глава 2"), ("paragraph", "третий")])

    def test_script_and_style_text_ignored(self):
        html = reader_page(1, 1, '<p>текст<script>var x = 1;</script><style>p{}</style></p>')
        self.assertEqual(loveread.parse_read_page(html)["blocks"][0]["text"], "текст")

    def test_page_without_reader_raises(self):
        with self.assertRaises(loveread.LoveReadError):
            loveread.parse_read_page(fixture("comments_36708.html"))


class DownloadBook(unittest.TestCase):
    def fake_site(self, pages, comments=None):
        comments = comments if comments is not None else fixture("comments_36708.html")
        calls = []

        def fetcher(url):
            calls.append(url)
            if "book-comments.php" in url:
                return comments
            for p, html in pages.items():
                if url.endswith(f"&p={p}"):
                    return html
            raise loveread.LoveReadError(f"unexpected url {url}")

        return fetcher, calls

    def test_collects_all_pages_in_order(self):
        pages = {
            1: reader_page(1, 3, "<p class=MsoNormal>один</p>"),
            2: reader_page(2, 3, "<p class=MsoNormal>два</p>"),
            3: reader_page(3, 3, "<p class=MsoNormal>три</p>"),
        }
        fetcher, calls = self.fake_site(pages)
        book = loveread.download_book(36708, fetcher=fetcher, delay=0)
        self.assertEqual(book["id"], 36708)
        self.assertEqual(book["title"], "Выкрикивается лот 49")
        self.assertEqual(book["author"], "Томас Пинчон")
        self.assertEqual(book["pages"], 3)
        self.assertEqual(book["pages_fetched"], 3)
        self.assertEqual(
            book["blocks"],
            [{"type": "paragraph", "page": 1, "text": "один"},
             {"type": "paragraph", "page": 2, "text": "два"},
             {"type": "paragraph", "page": 3, "text": "три"}],
        )
        self.assertEqual(len([c for c in calls if "read_book" in c]), 3)

    def test_page_mismatch_raises(self):
        pages = {1: reader_page(1, 2, "<p>a</p>"), 2: reader_page(1, 2, "<p>a</p>")}
        fetcher, _ = self.fake_site(pages)
        with self.assertRaises(loveread.LoveReadError):
            loveread.download_book(36708, fetcher=fetcher, delay=0)

    def test_max_pages_limits_fetch_and_is_recorded(self):
        pages = {1: reader_page(1, 3, "<p>a</p>"), 2: reader_page(2, 3, "<p>b</p>"), 3: reader_page(3, 3, "<p>c</p>")}
        fetcher, calls = self.fake_site(pages)
        book = loveread.download_book(36708, fetcher=fetcher, delay=0, max_pages=2)
        self.assertEqual((book["pages"], book["pages_fetched"], len(book["blocks"])), (3, 2, 2))
        self.assertEqual(len([c for c in calls if "read_book" in c]), 2)

    def test_missing_book_raises(self):
        fetcher, _ = self.fake_site({}, comments=fixture("comments_missing_index.html"))
        with self.assertRaises(loveread.LoveReadError):
            loveread.download_book(99999999, fetcher=fetcher, delay=0)

    def test_failure_mid_book_keeps_partial_result(self):
        pages = {1: reader_page(1, 3, "<p>один</p>")}
        fetcher, _ = self.fake_site(pages)
        with self.assertRaises(loveread.LoveReadError) as ctx:
            loveread.download_book(36708, fetcher=fetcher, delay=0)
        partial = ctx.exception.partial
        self.assertEqual(partial["complete"], False)
        self.assertEqual(partial["pages_fetched"], 1)
        self.assertEqual([b["text"] for b in partial["blocks"]], ["один"])

    def test_complete_book_is_marked_complete(self):
        fetcher, _ = self.fake_site({1: reader_page(1, 1, "<p>a</p>")})
        self.assertTrue(loveread.download_book(36708, fetcher=fetcher, delay=0)["complete"])

    def test_result_is_json_serialisable_and_to_text(self):
        pages = {1: reader_page(1, 1, '<div class="book-title">Глава 1</div><p>abc</p><p>def</p>')}
        fetcher, _ = self.fake_site(pages)
        book = loveread.download_book(36708, fetcher=fetcher, delay=0)
        json.dumps(book, ensure_ascii=False)
        self.assertEqual(loveread.book_to_text(book), "Выкрикивается лот 49\nТомас Пинчон\n\nГлава 1\n\nabc\n\ndef\n")


class Fetch(unittest.TestCase):
    def test_incomplete_read_is_retried(self):
        attempts = []

        def opener(url, timeout):
            attempts.append(url)
            if len(attempts) < 2:
                raise http.client.IncompleteRead(b"partial")
            return "ok"

        self.assertEqual(loveread.fetch("https://loveread.ec/x", opener=opener, retries=3, backoff=0), "ok")
        self.assertEqual(len(attempts), 2)

    def test_http_408_is_retried_and_404_is_not(self):
        def http_error(code):
            return urllib.error.HTTPError("https://loveread.ec/x", code, "msg", {}, None)

        calls = []

        def opener(url, timeout):
            calls.append(1)
            raise http_error(408 if len(calls) == 1 else 404)

        with self.assertRaises(loveread.LoveReadError):
            loveread.fetch("https://loveread.ec/x", opener=opener, retries=5, backoff=0)
        self.assertEqual(len(calls), 2)

    def test_unknown_charset_falls_back_to_utf8(self):
        class FakeResponse:
            headers = type("H", (), {"get_content_charset": staticmethod(lambda: "x-bogus-charset")})()

            def read(self):
                return "привет".encode("utf-8")

        self.assertEqual(loveread.decode_response(FakeResponse()), "привет")

    def test_retries_then_raises_on_persistent_failure(self):
        attempts = []

        def opener(url, timeout):
            attempts.append(url)
            raise OSError("boom")

        with self.assertRaises(loveread.LoveReadError):
            loveread.fetch("https://loveread.ec/x", opener=opener, retries=3, backoff=0)
        self.assertEqual(len(attempts), 3)


class Cli(unittest.TestCase):
    def test_invalid_numeric_options_are_rejected(self):
        for argv in (["1", "--delay", "-1"], ["1", "--retries", "0"], ["1", "--max-pages", "0"]):
            with self.assertRaises(SystemExit, msg=argv) as ctx:
                loveread.main(argv)
            self.assertEqual(ctx.exception.code, 2, argv)

    def test_txt_equal_to_output_is_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            loveread.main(["1", "-o", "same.json", "--txt", "same.json"])
        self.assertEqual(ctx.exception.code, 2)

    def test_partial_result_written_on_failure(self):
        pages = {1: reader_page(1, 2, "<p>один</p>")}
        comments = fixture("comments_36708.html")

        def fetcher(url):
            if "book-comments" in url:
                return comments
            if url.endswith("&p=1"):
                return pages[1]
            raise loveread.LoveReadError("network down")

        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "b.json"
            code = loveread.main(["36708", "-o", str(out), "--delay", "0", "-q"], fetcher=fetcher)
            self.assertEqual(code, 1)
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((saved["complete"], saved["pages_fetched"], len(saved["blocks"])), (False, 1, 1))


if __name__ == "__main__":
    unittest.main()
