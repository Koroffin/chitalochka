import json
import urllib.parse

from speedreader.sources import loveread


class SourceError(Exception):
    def __init__(self, message, pages_fetched=0):
        super().__init__(message)
        self.pages_fetched = pages_fetched


def normalize_url(url):
    url = url.strip().split("#", 1)[0]
    if "://" not in url:
        url = "https://" + url
    return url


class LoveReadSource:
    name = "loveread.ec"

    def matches(self, url):
        normalized = normalize_url(url)
        host = (urllib.parse.urlsplit(normalized).hostname or "").lower()
        if host != "loveread.ec" and not host.endswith(".loveread.ec"):
            return False
        try:
            loveread.parse_book_id(normalized)
        except loveread.LoveReadError:
            return False
        return True

    def key_for_url(self, url):
        return f"loveread-{loveread.parse_book_id(normalize_url(url))}"

    def download(self, url, log, should_stop):
        try:
            book_id = loveread.parse_book_id(normalize_url(url))
        except loveread.LoveReadError as e:
            raise SourceError(str(e)) from e

        def opener(u, timeout):
            if should_stop():
                raise loveread.LoveReadError("Отменено")
            return loveread._urllib_opener(u, timeout)

        def fetcher(u):
            return loveread.fetch(u, opener=opener)

        try:
            return loveread.download_book(book_id, fetcher=fetcher, log=log)
        except loveread.LoveReadError as e:
            pages = e.partial["pages_fetched"] if e.partial else 0
            raise SourceError(str(e), pages_fetched=pages) from e


SOURCES = [LoveReadSource()]


def detect(url):
    for source in SOURCES:
        if source.matches(url):
            return source
    return None


def book_key(book):
    prefix = "loveread" if "loveread.ec" in str(book.get("source", "")) else "import"
    return f"{prefix}-{book['id']}"


def import_book_json(path):
    not_a_book = SourceError("Файл не похож на книгу SpeedReader")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SourceError(f"Не удалось прочитать файл: {e}") from e
    if not isinstance(data, dict) or "id" not in data or "title" not in data:
        raise not_a_book
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not all(
        isinstance(b, dict) and isinstance(b.get("text"), str) for b in blocks
    ):
        raise not_a_book
    data.setdefault("author", "")
    data.setdefault("source", "")
    data.setdefault("complete", True)
    return data
