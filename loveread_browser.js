// Browser version of loveread.py. Open the book page
// https://loveread.ec/book-comments.php?book=<ID>, paste this file into the
// DevTools console (or run it through a browser automation tool) and wait.
// Pages are pulled with same-origin fetch(), so the tab never navigates.
// Result: a download of book_<ID>.json (same schema as loveread.py) and
// window.lovereadBook with the same object.
//
// Options can be preset before running: window.lovereadOptions = {maxPages: 3, download: false}
(async function lovereadDownload() {
  const opts = Object.assign({ delayMs: 500, retries: 3, maxPages: 0, download: true, timeoutMs: 30000 }, window.lovereadOptions || {});
  const log = (m) => console.log("[loveread] " + m);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (s) => s.split("\n").map((l) => l.replace(/[ \t\r\f\v\u00a0]+/g, " ").trim()).filter(Boolean).join("\n");

  function textOf(node) {
    let out = "";
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) out += child.nodeValue.replace(/\s+/g, " ");
      else if (child.nodeType !== Node.ELEMENT_NODE) continue;
      else if (child.tagName === "BR") out += "\n";
      else if (child.tagName === "SCRIPT" || child.tagName === "STYLE" || child.tagName === "TEMPLATE") continue;
      else if (child.tagName === "A" && /notes\.php/.test(child.getAttribute("href") || "")) continue; // footnote marker
      else out += textOf(child);
    }
    return out;
  }

  async function fetchText(url) {
    let lastError = "";
    for (let attempt = 1; attempt <= opts.retries; attempt++) {
      try {
        const resp = await fetch(url, { credentials: "same-origin", redirect: "follow", signal: AbortSignal.timeout(opts.timeoutMs) });
        if (resp.ok) return await resp.text();
        if (resp.status < 500 && resp.status !== 429) throw new Error(`HTTP ${resp.status} for ${url}`);
        lastError = `HTTP ${resp.status}`;
      } catch (e) {
        if (/^HTTP 4/.test(e.message)) throw e;
        lastError = e.message;
      }
      if (attempt < opts.retries) await sleep(1500 * attempt);
    }
    throw new Error(`giving up on ${url} after ${opts.retries} attempts (${lastError}); partial result in window.lovereadBook`);
  }

  function parseReadPage(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const content = doc.querySelector("#readerContent");
    if (!content) throw new Error("no #readerContent on the page: not a reading page");
    const nav = doc.querySelector(".navigation");
    const currentNode = nav && nav.querySelector(".current");
    if (!currentNode || !/^\d+$/.test(currentNode.textContent.trim())) throw new Error("pager has no current page number");
    const current = Number(currentNode.textContent.trim());
    const numbers = [current, ...[...nav.querySelectorAll("a")].map((a) => a.textContent.trim()).filter((t) => /^\d+$/.test(t)).map(Number)];
    const blocks = [];
    const selector = "p, h1, h2, h3, h4, .book-title";
    for (const node of content.querySelectorAll(selector)) {
      const outer = node.parentElement.closest(selector);
      if (outer && content.contains(outer)) continue; // nested block: the outer one already carries its text
      const text = clean(textOf(node));
      if (text) blocks.push({ type: node.tagName === "P" ? "paragraph" : "heading", text });
    }
    return { current, total: Math.max(...numbers), blocks };
  }

  const readLink = document.querySelector(".readBook a[href]");
  if (!readLink) throw new Error("no .readBook link on this page: open https://loveread.ec/book-comments.php?book=<ID> first");
  const readUrl = new URL(readLink.getAttribute("href"), location.href);
  const bookId = Number(readUrl.searchParams.get("id"));
  if (!bookId) throw new Error("read link has no numeric id: " + readUrl.pathname);
  const titleNode = document.querySelector(".blNameBook h2");
  const authorNode = document.querySelector(".booksAuthor a");
  const title = titleNode ? clean(textOf(titleNode)) : "";
  const author = authorNode ? clean(textOf(authorNode)).replace(/^Книги автора\s*[-:–—]?\s*/, "") : "";
  log(`${author} — ${title}`);

  const book = { id: bookId, title, author, source: location.href.split("#")[0], pages: null, pages_fetched: 0, complete: false, blocks: [] };
  window.lovereadBook = book; // partial result stays reachable if a page fails
  let page = 1;
  for (;;) {
    readUrl.searchParams.set("p", String(page));
    const parsed = parseReadPage(await fetchText(readUrl.href));
    if (parsed.current !== page) throw new Error(`asked for page ${page} but got page ${parsed.current}: the site redirected (partial result in window.lovereadBook)`);
    if (book.pages !== null && parsed.total !== book.pages) log(`warning: pager changed total from ${book.pages} to ${parsed.total} on page ${page}`);
    book.pages = parsed.total;
    for (const b of parsed.blocks) book.blocks.push({ type: b.type, page, text: b.text });
    book.pages_fetched = page;
    log(`page ${page}/${book.pages}: ${parsed.blocks.length} blocks`);
    if (page >= book.pages || (opts.maxPages && page >= opts.maxPages)) break;
    page += 1;
    if (opts.delayMs) await sleep(opts.delayMs);
  }
  book.complete = true;
  if (opts.download) {
    const blob = new Blob([JSON.stringify(book, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `book_${bookId}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }
  log(`done: ${book.blocks.length} blocks from ${page} of ${book.pages} pages (window.lovereadBook)`);
  return { id: bookId, title, author, pages: book.pages, pages_fetched: page, blocks: book.blocks.length };
})();
