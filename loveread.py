#!/usr/bin/env python3
"""Download a book from loveread.ec as structured text.

Usage: python3 loveread.py <book id or url> [-o book.json] [--txt book.txt]

Only the standard library is used. The comments page of the book
(book-comments.php?book=ID) gives the title, the author and the link to the
first reading page; the reading pages (read_book.php?id=ID&p=N) are walked
one by one until the last page reported by the pager.
"""
import argparse
import html
import http.client
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE_URL = "https://loveread.ec/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "area", "base", "col", "embed", "source", "wbr"}
# a browser closes an open <p> when one of these starts
P_CLOSERS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "li", "blockquote", "pre", "main", "section", "article"}
NON_TEXT_TAGS = {"script", "style", "template"}


class LoveReadError(Exception):
    """Any failure the caller should report instead of retrying blindly.

    `partial` holds the pages downloaded before the failure, when there are any.
    """

    def __init__(self, message, partial=None):
        super().__init__(message)
        self.partial = partial


# --- tiny DOM -----------------------------------------------------------------

class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.parent = parent

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def iter(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag=None, cls=None, id_=None):
        for node in self.iter():
            if tag and node.tag != tag:
                continue
            if cls and cls not in node.classes():
                continue
            if id_ and node.attrs.get("id") != id_:
                continue
            yield node

    def find(self, tag=None, cls=None, id_=None):
        return next(self.find_all(tag, cls, id_), None)

    def text(self, skip=lambda node: False):
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(re.sub(r"\s+", " ", child))
            elif child.tag == "br":
                parts.append("\n")
            elif child.tag not in NON_TEXT_TAGS and not skip(child):
                parts.append(child.text(skip))
        return "".join(parts)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        if tag in P_CLOSERS:
            self.handle_endtag("p")
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_html(text):
    builder = TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def clean_text(raw):
    lines = [re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)


# --- page parsers -------------------------------------------------------------

def parse_book_id(value):
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(value).query)
    for key in ("book", "id"):
        if query.get(key) and re.fullmatch(r"[0-9]+", query[key][0]):
            return int(query[key][0])
    raise LoveReadError(f"cannot find a numeric book id in {value!r}")


def parse_comments_page(text):
    doc = parse_html(text)
    block = doc.find(cls="readBook")
    link = block.find("a") if block else None
    if link is None or not link.attrs.get("href"):
        raise LoveReadError("no .readBook link on the page: wrong book id or the site layout changed")
    name_block = doc.find(cls="blNameBook")
    title_node = name_block.find("h2") if name_block else None
    author_node = doc.find(cls="booksAuthor")
    author = clean_text(author_node.text()) if author_node else ""
    author = re.sub(r"^Книги автора\s*[-:–—]?\s*", "", author)
    return {
        "title": clean_text(title_node.text()) if title_node else "",
        "author": author,
        "read_url": urllib.parse.urljoin(BASE_URL, html.unescape(link.attrs["href"])),
    }


def _is_footnote(node):
    return node.tag == "a" and "notes.php" in (node.attrs.get("href") or "")


def _ancestors(node):
    while node.parent is not None:
        node = node.parent
        yield node


def parse_read_page(text):
    doc = parse_html(text)
    content = doc.find(id_="readerContent")
    if content is None:
        raise LoveReadError("no #readerContent on the page: not a reading page")
    nav = doc.find(cls="navigation")
    if nav is None:
        raise LoveReadError("no .navigation pager on the page")
    current_node = nav.find(cls="current")
    if current_node is None or not clean_text(current_node.text()).isdigit():
        raise LoveReadError("pager has no current page number")
    current = int(clean_text(current_node.text()))
    numbers = [current]
    for a in nav.find_all("a"):
        t = clean_text(a.text())
        if t.isdigit():
            numbers.append(int(t))
    blocks = []
    emitted = set()
    for node in content.iter():
        if node.tag == "p":
            kind = "paragraph"
        elif node.tag in ("h1", "h2", "h3", "h4") or "book-title" in node.classes():
            kind = "heading"
        else:
            continue
        if any(id(anc) in emitted for anc in _ancestors(node)):
            continue
        emitted.add(id(node))
        body = clean_text(node.text(skip=_is_footnote))
        if body:
            blocks.append({"type": kind, "text": body})
    return {"current": current, "total": max(numbers), "blocks": blocks}


# --- network ------------------------------------------------------------------

def decode_response(response):
    charset = response.headers.get_content_charset() or "utf-8"
    data = response.read()
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _urllib_opener(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return decode_response(response)


def _retry_after(error, default):
    value = (error.headers or {}).get("Retry-After") if hasattr(error, "headers") else None
    if value and re.fullmatch(r"[0-9]+", value.strip()):
        return min(int(value), 120)
    return default


def fetch(url, opener=None, retries=3, backoff=1.5, timeout=30):
    opener = opener or _urllib_opener
    last_error = None
    for attempt in range(1, retries + 1):
        wait = backoff * attempt
        try:
            return opener(url, timeout)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                raise LoveReadError(f"{url}: redirect loop (HTTP {e.code}), probably the book does not exist") from e
            if e.code < 500 and e.code not in (408, 429):
                raise LoveReadError(f"{url}: HTTP {e.code}") from e
            last_error = f"HTTP {e.code}"
            wait = _retry_after(e, wait)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            last_error = str(e)
        if attempt < retries:
            time.sleep(wait)
    raise LoveReadError(f"{url}: giving up after {retries} attempts ({last_error})")


# --- book download ------------------------------------------------------------

def page_url(read_url, page):
    parts = urllib.parse.urlsplit(read_url)
    query = urllib.parse.parse_qs(parts.query)
    query["p"] = [str(page)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, doseq=True)))


def download_book(book_id, fetcher=None, delay=0.5, max_pages=None, log=lambda msg: None):
    fetcher = fetcher or fetch
    comments_url = f"{BASE_URL}book-comments.php?book={book_id}"
    info = parse_comments_page(fetcher(comments_url))
    log(f"{info['author']} — {info['title']}")
    book = {
        "id": book_id,
        "title": info["title"],
        "author": info["author"],
        "source": comments_url,
        "pages": None,
        "pages_fetched": 0,
        "complete": False,
        "blocks": [],
    }
    page = 1
    try:
        while True:
            parsed = parse_read_page(fetcher(page_url(info["read_url"], page)))
            if parsed["current"] != page:
                raise LoveReadError(
                    f"asked for page {page} but got page {parsed['current']}: "
                    f"the site redirected, the book probably has fewer pages"
                )
            if book["pages"] is not None and parsed["total"] != book["pages"]:
                log(f"warning: pager changed total from {book['pages']} to {parsed['total']} on page {page}")
            book["pages"] = parsed["total"]
            for block in parsed["blocks"]:
                book["blocks"].append({"type": block["type"], "page": page, "text": block["text"]})
            book["pages_fetched"] = page
            log(f"page {page}/{book['pages']}: {len(parsed['blocks'])} blocks")
            if page >= book["pages"] or (max_pages and page >= max_pages):
                break
            page += 1
            if delay:
                time.sleep(delay)
    except LoveReadError as e:
        raise LoveReadError(str(e), partial=book if book["pages_fetched"] else None) from e
    except KeyboardInterrupt as e:
        raise LoveReadError("interrupted", partial=book if book["pages_fetched"] else None) from e
    book["complete"] = True
    return book


def book_to_text(book):
    header = "\n".join(line for line in (book["title"], book["author"]) if line)
    return header + "\n\n" + "\n\n".join(b["text"] for b in book["blocks"]) + "\n"


def _positive(kind, minimum):
    def convert(text):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}")
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}")
        return value
    return convert


def write_outputs(book, output, txt):
    with open(output, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=1)
    if txt:
        with open(txt, "w", encoding="utf-8") as f:
            f.write(book_to_text(book))


def main(argv=None, fetcher=None):
    parser = argparse.ArgumentParser(description="Download a loveread.ec book as JSON (and optionally plain text).")
    parser.add_argument("book", help="book id or any loveread.ec url with book=ID / id=ID")
    parser.add_argument("-o", "--output", help="json file (default: book_<id>.json)")
    parser.add_argument("--txt", help="also write plain text to this file")
    parser.add_argument("--delay", type=_positive(float, 0), default=0.5, help="seconds between page requests (default 0.5)")
    parser.add_argument("--retries", type=_positive(int, 1), default=3)
    parser.add_argument("--max-pages", type=_positive(int, 1), help="stop after N pages (for testing)")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)
    if args.txt and args.output and pathlib.Path(args.txt).resolve() == pathlib.Path(args.output).resolve():
        parser.error("--txt and -o point to the same file")

    log = (lambda msg: None) if args.quiet else (lambda msg: print(msg, file=sys.stderr, flush=True))
    try:
        book_id = parse_book_id(args.book)
    except LoveReadError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    output = args.output or f"book_{book_id}.json"
    fetcher = fetcher or (lambda url: fetch(url, retries=args.retries))
    try:
        book = download_book(book_id, fetcher=fetcher, delay=args.delay, max_pages=args.max_pages, log=log)
    except LoveReadError as e:
        print(f"error: {e}", file=sys.stderr)
        if e.partial:
            try:
                write_outputs(e.partial, output, args.txt)
                print(f"partial result ({e.partial['pages_fetched']} pages) saved to {output}", file=sys.stderr)
            except OSError as write_error:
                print(f"error: cannot write partial result: {write_error}", file=sys.stderr)
        return 1

    try:
        write_outputs(book, output, args.txt)
    except OSError as e:
        print(f"error: cannot write output: {e}", file=sys.stderr)
        return 1
    log(f"saved {len(book['blocks'])} blocks from {book['pages']} pages to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
