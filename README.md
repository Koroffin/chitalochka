# loveread parser

Downloads a book from loveread.ec into JSON for the speed-reading tool.

## Python (no dependencies, Python 3.8+)

    python3 loveread.py 36708                      # -> book_36708.json
    python3 loveread.py https://loveread.ec/book-comments.php?book=36708 -o book.json --txt book.txt
    python3 loveread.py 36708 --max-pages 2         # quick check, two pages only

Options: `--delay` seconds between pages (default 0.5), `--retries` (default 3), `-q` silent.
Exit code 1 with an `error: ...` line on stderr when the book does not exist,
the layout changed, or the network keeps failing.

## Browser

Open `https://loveread.ec/book-comments.php?book=<ID>`, paste `loveread_browser.js`
into the DevTools console. It fetches all pages without leaving the tab, saves
`book_<ID>.json` through the browser download and keeps the object in
`window.lovereadBook`. Options before pasting:

    window.lovereadOptions = { maxPages: 3, download: false, delayMs: 500 }

## Output

    {
      "id": 36708, "title": "...", "author": "...",
      "source": "https://loveread.ec/book-comments.php?book=36708",
      "pages": 46, "pages_fetched": 46, "complete": true,
      "blocks": [ {"type": "paragraph" | "heading", "page": 1, "text": "..."}, ... ]
    }

Footnote markers `[n]` are dropped, `<br>` inside a paragraph becomes `\n`,
other whitespace is collapsed as a browser would. `heading` comes from the
site's chapter titles. If the download stops halfway (network, Ctrl-C, redirect
to the last page), the pages fetched so far are still written with
`"complete": false`; the browser version keeps them in `window.lovereadBook`.

## Tests

    python3 -m unittest discover -s tests -t .
