# SpeedReader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Настольное приложение PySide6: библиотека скачанных с loveread.ec книг, чтение с бегущей по словам подсветкой, закладки, сборка exe и deb в GitHub Actions.

**Architecture:** Логика без Qt в трёх модулях (`reading.py` — слова, тайминги, навигация; `library.py` — хранилище; `sources/` — скачивание и импорт), поверх них три экрана в `ui/` и `MainWindow` со стеком. Парсер loveread переезжает в `sources/loveread.py` без изменения поведения.

**Tech Stack:** Python 3.12, PySide6-Essentials ≥ 6.7, unittest, PyInstaller, dpkg-deb, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-24-speedreader-design.md`

## Global Constraints

- Python 3.12; зависимость только `PySide6-Essentials>=6.7`; остальное — стандартная библиотека.
- Схема книги (контракт источника): `{id, title, author, source, pages, pages_fetched, complete, blocks: [{type: paragraph|heading, page, text}]}`.
- `library.json` версии 1, запись атомарная (временный файл + `os.replace`).
- Тесты: `cd /home/nikita-korovin/projects/reader && QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -t .` — зелёные после каждой задачи. Виртуальное окружение `.venv` в корне проекта, команды Python через `.venv/bin/python`.
- Весь текст интерфейса по-русски; комментарии в коде только на «почему», не больше двух на 100 строк.
- Скорость: ползунок 100–1000 слов/мин, шаг 10, значение по умолчанию 250.
- Поведение парсера `loveread` не меняется: 32 существующих теста проходят без правок логики.

## Review Focus

1. Книга без единого слова (все блоки пустые или только знаки): модель чтения не падает, прогресс 0, читалка показывает текст и «Старт» ничего не ломает. Тест в задаче 1.
2. Книга на 60 000 слов: построение документа и первый кадр подсветки укладываются в 2 с, прокрутка не дёргается. Тест в задаче 4.
3. Позиция или закладка из `library.json` за пределами книги (книга перекачана и стала короче): обрезается, а не падает. Тест в задаче 2.
4. URL с пробелами, без схемы, с `http://`, с `www.` или с хвостом `#...`: распознаётся как loveread. Тест в задаче 3.
5. Каталог данных недоступен на запись: ошибка показывается в строке состояния, приложение не падает и чтение продолжается. Тест в задаче 2 (исключение `LibraryError`) и задаче 6 (строка состояния).

---

### Task 0: Каркас пакета и переезд парсера

**Files:**
- Create: `pyproject.toml`, `speedreader/__init__.py`, `speedreader/__main__.py`, `speedreader/sources/__init__.py` (пустой пока), `.gitignore`
- Move: `loveread.py` → `speedreader/sources/loveread.py`; `loveread.py` остаётся обёрткой из `from speedreader.sources.loveread import main; raise SystemExit(main())`
- Modify: `tests/test_loveread.py:1-10` (импорт `from speedreader.sources import loveread`)
- Test: существующий `tests/test_loveread.py`

**Interfaces:**
- Produces: пакет `speedreader`, `speedreader.__version__ = "0.0.0"`, `speedreader.sources.loveread` с прежним API (`download_book`, `fetch`, `_urllib_opener`, `parse_book_id`, `LoveReadError`).

- [ ] **Step 1: Создать окружение** — `python3 -m venv .venv && .venv/bin/pip install "PySide6-Essentials>=6.7" pyinstaller`; записать `.gitignore` (`.venv/`, `__pycache__/`, `dist/`, `build/`, `*.json` в корне кроме фикстур — точнее: `/book_*.json`).
- [ ] **Step 2: Написать `pyproject.toml`** — `[project] name="speedreader"`, `version` динамически не нужен: `version="0.0.0"`, `requires-python=">=3.12"`, `dependencies=["PySide6-Essentials>=6.7"]`, `[project.scripts] speedreader="speedreader.app:main"`, сборка `setuptools`, пакеты `speedreader*`.
- [ ] **Step 3: Переместить парсер и поправить импорт в тестах**; `speedreader/__main__.py`: `from speedreader.app import main; raise SystemExit(main())` (модуль `app` появится в задаче 6, до этого `python -m speedreader` падает — допустимо).
- [ ] **Step 4: Прогнать тесты** — Run: `.venv/bin/python -m unittest discover -s tests -t .` Expected: `Ran 32 tests ... OK`.
- [ ] **Step 5: Проверить CLI-обёртку** — Run: `.venv/bin/python loveread.py abc; echo $?` Expected: `error: cannot find a numeric book id in 'abc'` и `1`.
- [ ] **Step 6: Commit** `chore: package skeleton, move loveread parser into speedreader.sources`

### Task 1: Модель чтения `speedreader/reading.py`

**Files:**
- Create: `speedreader/reading.py`
- Test: `tests/test_reading.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Word(block: int, index: int, start: int, end: int, text: str, last_in_block: bool)` со свойством `position -> tuple[int, int]` — `start/end` смещения в `blocks[block]["text"]`, `text == block_text[start:end]`.
  - `split_words(text: str) -> list[tuple[int, int]]` — спаны слов по правилу спеки (токен без буквы/цифры приклеивается к предыдущему, в начале — к следующему).
  - `ends_sentence(text: str) -> bool`, `ends_clause(text: str) -> bool`.
  - `delay_for(word: Word, wpm: int) -> float` — секунды: `60/wpm * max(множителей)`; 1.5 для `,;:` и тире `-–—` в конце, 2.0 для конца предложения, 2.5 для `last_in_block`.
  - `class ReadingModel(book: dict)`: `words: list[Word]`, `word_count: int`, `pos: int` (плоский индекс, 0 при пустой книге), `current -> Word | None`, `position -> tuple[int, int]`, `set_position(block: int, word: int) -> None` (обрезает), `flat_index(block, word) -> int`, `advance() -> bool`, `delay(wpm: int) -> float`, `prev_sentence()`, `next_sentence()`, `prev_block()`, `next_block()`, `progress -> float`, `is_sentence_start(i: int) -> bool`.

- [ ] **Step 1: Тесты разбиения и таймингов** (`tests/test_reading.py`):

```python
def test_split_words_keeps_punctuation_attached(self):
    self.assertEqual(spans("Привет, мир."), ["Привет,", "мир."])
def test_lonely_dash_glued_to_previous_word(self):
    self.assertEqual(spans("— Привет, — сказал он."), ["— Привет, —", "сказал", "он."])  # первое тире клеится к следующему слову, второе к предыдущему
def test_only_punctuation_block_has_no_words(self):
    self.assertEqual(spans("* * *"), [])
def test_ends_sentence_handles_closing_quotes(self):
    for t in ["конец.", "конец?»", "конец!)", "конец…"]: self.assertTrue(ends_sentence(t))
    for t in ["нет,", "нет", "т.е."]: ...  # "т.е." считается концом: правило простое, тест фиксирует это
def test_delay_uses_max_multiplier_not_product(self):
    w = Word(0, 0, 0, 5, "конец.", last_in_block=True)
    self.assertAlmostEqual(delay_for(w, 250), 60 / 250 * 2.5)
def test_delay_clause(self):  # "слово," при 300 wpm == 0.3
```

- [ ] **Step 2: Тесты модели**: книга из блоков `["Раз два. Три четыре!", "Пять шесть."]` и заголовка:
  - `test_word_count_and_positions` — `word_count == 6`, `words[2].position == (0, 2)`, `words[3].last_in_block`.
  - `test_advance_stops_at_end` — после 5 `advance()` шестой возвращает `False`, `pos == 5`.
  - `test_set_position_clamps` — `set_position(9, 9)` даёт `position == (1, 1)`; `set_position(0, 99)` даёт `(0, 3)`.
  - `test_prev_sentence_goes_to_start_of_current_then_previous` — с `pos=3` («четыре!») → `pos=2`; ещё раз → `pos=0`.
  - `test_next_sentence` — с `pos=0` → `2`; с `pos=4` → `5` (последнее слово).
  - `test_block_navigation` — `next_block()` с 0 → 4; `prev_block()` с 5 → 4; ещё раз → 0.
  - `test_progress` — `pos=3` даёт `0.5`.
  - `test_empty_book` — `ReadingModel({"blocks": []})`: `word_count == 0`, `current is None`, `advance()` False, `progress == 0.0`, навигация не падает.
- [ ] **Step 3: Run** `.venv/bin/python -m unittest tests.test_reading -v` Expected: FAIL/ERROR на каждом тесте (`No module named 'speedreader.reading'`).
- [ ] **Step 4: Реализовать `speedreader/reading.py`** по интерфейсам выше. Разбиение: `re.finditer(r"\S+", text)`; токен считается словом, если `re.search(r"[^\W_]", tok)`. Граница предложения для навигации: `is_sentence_start(i)` истинно, если `i == 0`, или `words[i-1].last_in_block`, или `ends_sentence(words[i-1].text)`.
- [ ] **Step 5: Run** тесты файла — PASS; затем весь набор — OK.
- [ ] **Step 6: Commit** `feat: reading model with word splitting, timing and navigation`

### Task 2: Хранилище `speedreader/library.py`

**Files:**
- Create: `speedreader/library.py`
- Test: `tests/test_library.py` (временный каталог через `tempfile.TemporaryDirectory`)

**Interfaces:**
- Consumes: `ReadingModel` для подсчёта `word_count` при добавлении (`speedreader.reading`).
- Produces:
  - `class LibraryError(Exception)`.
  - `class LibraryStore(data_dir: pathlib.Path)`: `entries -> list[dict]` (новые первыми), `wpm -> int` (свойство с сеттером, сохраняет), `find(key) -> dict | None`, `add_book(key: str, book: dict) -> dict` (пишет `books/<key>.json`; если запись есть — заменяет файл, сохраняет `position` и `bookmarks`, обрезая их), `load_book(key) -> dict` (`LibraryError` при отсутствии/битом файле), `is_available(key) -> bool`, `set_position(key, block, word)`, `reset_position(key)`, `add_bookmark(key, block, word, label) -> bool`, `remove_bookmark(key, index)`, `remove(key)` (файл и запись), `save()`.
  - Формат записи — как в спеке (`key, title, author, source_url, added_at, complete, word_count, position, bookmarks`).
  - `default_data_dir() -> pathlib.Path` через `QStandardPaths.writableLocation(AppDataLocation)` с запасным `~/.local/share/SpeedReader`, живёт в `speedreader/app.py` (задача 6), не здесь: `library.py` не импортирует Qt.

- [ ] **Step 1: Тесты**:
  - `test_add_book_writes_file_and_entry` — после `add_book("loveread-1", book)`: `books/loveread-1.json` существует, `entries[0]["word_count"] == 6`, `position == {"block": 0, "word": 0}`.
  - `test_persists_between_instances` — второй `LibraryStore(tmp)` видит запись и `wpm`, выставленный первым (`store.wpm = 300`).
  - `test_broken_library_json_is_renamed_and_store_starts_empty` — записать `{` в `library.json`; `entries == []`, файл `library.json.broken` есть.
  - `test_readd_keeps_position_and_clamps` — позиция `(1, 1)` и закладка `(1, 1)`; повторный `add_book` с книгой из одного блока «Раз два» → позиция `(0, 1)`, закладка `(0, 1)`.
  - `test_missing_book_file` — удалить файл: `is_available` False, `load_book` бросает `LibraryError`.
  - `test_bookmark_no_duplicates_and_remove` — второй `add_bookmark` на том же месте возвращает False; `remove_bookmark(key, 0)` очищает.
  - `test_reset_position_keeps_bookmarks`.
  - `test_remove_deletes_file_and_entry`.
  - `test_save_is_atomic` — после `save()` нет файлов `*.tmp` в каталоге; содержимое читается как JSON.
  - `test_unwritable_dir_raises_library_error` — `data_dir` внутри файла (не каталога): конструктор или `save()` бросает `LibraryError`, не `OSError`.
- [ ] **Step 2: Run** — ошибки импорта.
- [ ] **Step 3: Реализовать**. `added_at = datetime.now().isoformat(timespec="seconds")`. Обрезка позиции через `ReadingModel(book).set_position` + `position`.
- [ ] **Step 4: Run** — PASS, весь набор OK.
- [ ] **Step 5: Commit** `feat: library store with bookmarks and atomic save`

### Task 3: Источники `speedreader/sources/__init__.py`

**Files:**
- Modify: `speedreader/sources/__init__.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `loveread.parse_book_id`, `loveread.download_book`, `loveread.fetch`, `loveread._urllib_opener`, `loveread.LoveReadError`.
- Produces:
  - `class SourceError(Exception)` с атрибутом `pages_fetched: int` (0, если неизвестно).
  - `class LoveReadSource`: `name = "loveread.ec"`, `matches(url) -> bool`, `key_for_url(url) -> str` (`loveread-<id>`), `download(url, log, should_stop) -> dict` — `should_stop: Callable[[], bool]` проверяется в `opener` перед каждой HTTP-попыткой; при `True` бросает `SourceError("Отменено")`; `LoveReadError` → `SourceError(str(e), pages_fetched=e.partial["pages_fetched"] if e.partial else 0)`.
  - `normalize_url(url) -> str` — strip, без `#...`, добавляет `https://`, если схемы нет.
  - `detect(url) -> LoveReadSource | None`.
  - `book_key(book: dict) -> str` — `loveread-<id>`, если `source` содержит `loveread.ec`, иначе `import-<id>`.
  - `import_book_json(path) -> dict` — `SourceError`, если файл не JSON или нет `id`/`title`/`blocks` со строковыми `text`.
  - `SOURCES = [LoveReadSource()]`.

- [ ] **Step 1: Тесты**:
  - `test_detect_variants` — для `" https://loveread.ec/book-comments.php?book=36708#x "`, `"loveread.ec/read_book.php?id=36708&p=3"`, `"http://www.loveread.ec/book-comments.php?book=36708"` `detect` возвращает источник и `key_for_url == "loveread-36708"`.
  - `test_detect_unknown_returns_none` — `"https://flibusta.is/b/1"`.
  - `test_download_uses_fetcher_and_maps_errors` — через `unittest.mock.patch("speedreader.sources.loveread.download_book")` проверяется, что `download` вызывает его с `book_id=36708`; подменённый `download_book`, бросающий `LoveReadError("x", partial={"pages_fetched": 7})`, даёт `SourceError` с `pages_fetched == 7`.
  - `test_should_stop_aborts_before_http` — `should_stop=lambda: True`, `_urllib_opener` подменён на функцию, которая поднимает `AssertionError`: `download` бросает `SourceError`, а подмена не вызвана.
  - `test_import_json_valid_and_invalid` — валидный файл возвращает dict; файл `{"id": 1}` даёт `SourceError`; не-JSON даёт `SourceError`.
  - `test_book_key`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Реализовать**.
- [ ] **Step 4: Run** — PASS; набор OK.
- [ ] **Step 5: Commit** `feat: source registry with loveread download, cancel and json import`

### Task 4: Экран чтения `speedreader/ui/reader_page.py`

**Files:**
- Create: `speedreader/ui/__init__.py`, `speedreader/ui/reader_page.py`
- Test: `tests/test_reader_page.py` (offscreen; общий `QApplication` создаётся в `setUpModule`, `QT_QPA_PLATFORM=offscreen` ставится в тесте через `os.environ.setdefault` до импорта Qt)

**Interfaces:**
- Consumes: `ReadingModel`, `Word`, `delay_for`; `LibraryStore` (`set_position`, `add_bookmark`, `remove_bookmark`, `wpm`, `find`).
- Produces: `class ReaderPage(QWidget)`: `__init__(store: LibraryStore)`, `load(key: str, book: dict, restart: bool = False)`, `model: ReadingModel`, `playing -> bool`, `play()`, `pause()`, `toggle()`, `refresh()` (перерисовать подсветку, прокрутку и статус), `current_delay_ms() -> int`, `jump_to_document_position(pos: int)`, `on_user_scrolled()`, `add_bookmark()`, `save_position()`, `wpm_slider: QSlider`, `text: QTextEdit`, `bookmarks_box: QComboBox`, `word_offsets: list[int]` (позиция в документе для каждого `model.words[i]`), сигнал `back_requested = Signal()`, статус `status_label: QLabel` с «N %».

- [ ] **Step 1: Тесты**:
  - `test_document_offsets_match_words` — после `load`: для каждого `i` текст документа `doc.toPlainText()[off:off+len(w.text)] == w.text`.
  - `test_highlight_follows_position` — `model.set_position(1, 0)`; `page.refresh()`; единственная `extraSelections()` покрывает слово с `word_offsets[idx]`.
  - `test_play_advances_with_timer` — `store.wpm = 1000`; `play()`; `QTest.qWait(400)` → `model.pos >= 2`; `pause()`; позиция в `store.find(key)["position"]` обновлена.
  - `test_keys` — `QTest.keyClick(page, Qt.Key_Right)` → `pos` на старте следующего предложения; `Qt.Key_Space` переключает `playing`; `Qt.Key_Escape` испускает `back_requested`.
  - `test_slider_changes_store_wpm` — `wpm_slider.setValue(400)` → `store.wpm == 400`; `model.delay(400)` используется на следующем тике (проверка через `page.current_delay_ms() == round(delay_for(...)*1000)`).
  - `test_click_on_word_moves_position` — `page.jump_to_document_position(word_offsets[4] + 1)` → `pos == 4`.
  - `test_bookmark_added_and_listed` — `page.add_bookmark()` → в `bookmarks_box` 1 элемент, в `store` 1 закладка; повтор не добавляет.
  - `test_manual_scroll_pauses` — `play()`; `page.on_user_scrolled()` → `playing is False`.
  - `test_large_book_builds_fast` — книга из 60 000 слов (1 000 блоков по 60 слов): `load` + первый `refresh` быстрее 2 с (`time.perf_counter`).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Реализовать**. Документ строится один раз через `QTextCursor` по `model.blocks`: заголовок — `QTextCharFormat` жирный, 1.3× размера; абзац — отступ первой строки 24 px, `setDocumentMargin(24)`; ширина колонки — `setMaximumWidth(760)` на `text` с центрированием в `QHBoxLayout`. Смещения: `word_offsets.append(block_start + w.start)`. Подсветка: `ExtraSelection` с `QColor("#ffe58a")` (светлый жёлтый, тёмный текст). Прокрутка: `setTextCursor` на начало слова + `ensureCursorVisible()`, если курсор ниже 2/3 высоты вьюпорта — `centerCursor()`. Таймер: `QTimer` single-shot, `start(current_delay_ms())` после каждого шага. Пауза ставится также в `hideEvent`. Ручная прокрутка: подписка на `verticalScrollBar().sliderPressed` и `wheelEvent` вьюпорта через `eventFilter` → `on_user_scrolled()`; программная прокрутка помечается флагом, чтобы не ставить паузу самой себе. Клик: `mousePressEvent` на вьюпорте → `cursorForPosition(...).position()` → `jump_to_document_position` (бинарный поиск по `word_offsets`). Панель внизу: `QToolButton` старт/пауза, `QSlider` 100–1000 шаг 10 с `QLabel` значения, кнопка «Закладка» (Ctrl+B), `QComboBox` закладок (выбор → переход; контекстное меню «Удалить»), кнопка «В библиотеку», `status_label`.
- [ ] **Step 4: Run** — PASS; набор OK.
- [ ] **Step 5: Commit** `feat: reader page with running highlight, speed slider and bookmarks`

### Task 5: Форма источника `speedreader/ui/add_page.py`

**Files:**
- Create: `speedreader/ui/add_page.py`
- Test: `tests/test_add_page.py` (offscreen, без сети: источник подменяется через атрибут `page.sources`)

**Interfaces:**
- Consumes: `detect`, `SOURCES`, `SourceError`, `import_book_json`, `book_key`; `LibraryStore.add_book`, `find`.
- Produces: `class DownloadWorker(QThread)`: `__init__(source, url)`, сигналы `progress = Signal(int, int)` (страница, всего), `finished_ok = Signal(dict)`, `failed = Signal(str)`, метод `cancel()`; `class AddPage(QWidget)`: `__init__(store)`, `sources: list` (по умолчанию `SOURCES`, тесты подменяют), `url_edit: QLineEdit`, `download_button`, `import_button`, `pdf_button` (disabled, tooltip «Скоро: чтение из PDF»), `cancel_button`, `back_button`, `progress: QProgressBar`, `error_label: QLabel`, `busy -> bool`, `start_download()`, `import_json(path: str)`, `request_cancel()`, `shutdown(timeout_ms: int = 3000)`, сигналы `book_ready = Signal(str)` (key), `back_requested = Signal()`.

- [ ] **Step 1: Тесты** с `FakeSource` (атрибуты как у `LoveReadSource`; `download` вызывает `log("page 1/2: ...")`, `log("page 2/2: ...")`, проверяет `should_stop()` между ними и возвращает книгу):
  - `test_unknown_url_shows_error_without_thread` — `url_edit.setText("https://example.com")`; `start_download()` → `error_label.text()` содержит «loveread.ec», `busy` False.
  - `test_download_adds_book_and_emits_key` — `QTest.qWait` до `book_ready`; `store.find("fake-1")` есть; `progress.value() == 2`; книга в библиотеке открывается с первого слова (позиция `(0,0)`).
  - `test_existing_key_skips_download` — книга уже в `store`: `book_ready` испускается сразу, `FakeSource.download` не вызван.
  - `test_error_text_mentions_pages_fetched` — `download` бросает `SourceError("сеть", pages_fetched=3)` → `error_label.text()` содержит `3`; `store.entries` пуст.
  - `test_cancel_unblocks_and_keeps_nothing` — `FakeSource` ждёт `should_stop()`; `request_cancel()` → после `qWait` `busy` False, `store.entries` пуст, `cancel_button.text()` снова «Отмена».
  - `test_buttons_locked_while_busy` — во время загрузки `download_button`, `import_button`, `back_button` disabled.
  - `test_import_json` — временный файл с валидной книгой → `book_ready`; с невалидной → `error_label`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Реализовать**. Прогресс парсится из `log` регулярным выражением `page (\d+)/(\d+)`. Сигналы потока подключаются с `Qt.QueuedConnection`; у каждой загрузки номер поколения, результаты старых игнорируются. `shutdown` ставит флаг отмены и `wait(timeout_ms)`.
- [ ] **Step 4: Run** — PASS; набор OK.
- [ ] **Step 5: Commit** `feat: add page with background download, cancel and json import`

### Task 6: Библиотека, главное окно, запуск `speedreader/ui/library_page.py`, `speedreader/app.py`

**Files:**
- Create: `speedreader/ui/library_page.py`, `speedreader/app.py`
- Test: `tests/test_app.py` (offscreen)

**Interfaces:**
- Consumes: все предыдущие.
- Produces: `class LibraryPage(QWidget)`: `__init__(store)`, `refresh()`, `list: QListWidget` (строка: название, автор, «N %», при недоступном файле «файл книги не найден»), кнопки `continue_button`, `restart_button`, `redownload_button`, `delete_button`, `add_button`, сигналы `open_requested = Signal(str, bool)` (key, restart), `add_requested = Signal()`, `redownload_requested = Signal(str)`; `class MainWindow(QMainWindow)`: `__init__(store)`, `stack: QStackedWidget`, `library_page`, `add_page`, `reader_page`, `show_library()`, `open_book(key, restart)`, `closeEvent` (сохранить позицию, `add_page.shutdown()`), ошибки `LibraryError` → `statusBar().showMessage(...)` на 10 с; `default_data_dir() -> Path`; `main(argv: list[str] | None = None) -> int` с флагом `--smoke` (окно создаётся и закрывается через `QTimer.singleShot(0, app.quit)`, возврат 0) и `--data-dir PATH`.

- [ ] **Step 1: Тесты**:
  - `test_library_lists_books_with_progress` — две книги, одна с позицией на середине → в строке «50 %».
  - `test_continue_and_restart_signals` — выбор строки + `continue_button.click()` → `open_requested("k", False)`; `restart_button.click()` → `("k", True)` и позиция в `store` сброшена.
  - `test_missing_file_disables_continue` — удалить файл книги, `refresh()`: `continue_button.isEnabled()` False, `redownload_button` True, строка содержит «не найден».
  - `test_delete_asks_and_removes` — `QMessageBox.question` подменён на `Yes` через `unittest.mock.patch` → запись удалена; подменён на `No` → осталась.
  - `test_main_window_flow` — `MainWindow(store)`: `open_book("k", False)` показывает `reader_page`; `reader_page.back_requested` возвращает библиотеку и сохраняет позицию; `add_page.book_ready` открывает читалку с `(0,0)`.
  - `test_close_saves_position` — `reader_page.model.set_position(0, 2)`; `window.close()` → `store.find("k")["position"] == {"block": 0, "word": 2}`.
  - `test_store_error_goes_to_status_bar` — `store.save` подменён на функцию, бросающую `LibraryError("диск")` → `statusBar().currentMessage()` содержит «диск», окно живо.
  - `test_main_smoke_returns_zero` — `main(["--smoke", "--data-dir", tmp]) == 0`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Реализовать**. Окно 1000×700, заголовок «SpeedReader», иконка из `assets/icon.png` (через `importlib.resources`, файл кладётся в `speedreader/assets/icon.png` — задача 7 создаёт; до неё иконка отсутствует и просто не ставится). `QApplication.setApplicationName("SpeedReader")`, `setOrganizationName("SpeedReader")` до вычисления `default_data_dir`.
- [ ] **Step 4: Run** — PASS; набор OK; `.venv/bin/python -m speedreader --smoke; echo $?` → `0`; ручной запуск `.venv/bin/python -m speedreader` с реальной книгой 36708 (скачать через форму) — подсветка бежит, Esc возвращает в библиотеку, повторный запуск продолжает с места.
- [ ] **Step 5: Commit** `feat: library page, main window and entry point`

### Task 7: Иконка, PyInstaller, deb, GitHub Actions

**Files:**
- Create: `assets/icon.svg`, `assets/make_icons.py`, `speedreader/assets/icon.png` (256×256), `assets/icon.ico`, `packaging/speedreader.spec`, `packaging/speedreader.desktop`, `packaging/build_deb.sh`, `packaging/set_version.py`, `.github/workflows/build.yml`
- Modify: `README.md` (разделы «Установка» и «Сборка»), `pyproject.toml` (`package-data` для `speedreader/assets/*`)
- Test: `tests/test_packaging.py` (без сборки: проверяет `set_version.py` и валидность YAML/desktop)

**Interfaces:**
- Consumes: `speedreader.app:main`, флаг `--smoke`.
- Produces: артефакты `SpeedReader.exe` (Windows, onefile) и `speedreader_<ver>_amd64.deb`.

- [ ] **Step 1: Тесты** `tests/test_packaging.py`:
  - `test_set_version_rewrites_init` — на копии `__init__.py` во временном каталоге `set_version.py 1.2.3-rc1 <path>` пишет `__version__ = "1.2.3rc1"` и печатает `deb=1.2.3~rc1 pep440=1.2.3rc1 prerelease=true`; для `1.2.3` — `prerelease=false`.
  - `test_workflow_yaml_parses` — `yaml` недоступен в stdlib: проверять `python -c` не получится; вместо этого тест читает файл и проверяет наличие строк `runs-on: windows-latest`, `runs-on: ubuntu-22.04`, `on:` с `tags:` и `workflow_dispatch`.
  - `test_desktop_file_has_required_keys` — `Exec=/usr/bin/speedreader`, `Icon=speedreader`, `Type=Application`, `Name=SpeedReader`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Иконка** — `assets/icon.svg` (открытая книга, одна строка подсвечена жёлтой полосой, 256×256); `assets/make_icons.py` рисует ту же картинку через Pillow (`ImageDraw`) в 16/32/48/64/128/256 и пишет `speedreader/assets/icon.png` и `assets/icon.ico` (`img.save(..., sizes=[...])`); запустить и закоммитить результат. Pillow — только инструмент разработчика, в `pyproject` не попадает.
- [ ] **Step 4: `packaging/set_version.py <tag-or-version> [init-path]`** — отбрасывает `v`, разбирает `X.Y.Z` и необязательный `-suffix`; пишет `__version__`; печатает одну строку `deb=... pep440=... prerelease=...`; для `0.0.0.dev<N>` — `deb=0.0.0~dev<N>`.
- [ ] **Step 5: `packaging/speedreader.spec`** — `Analysis([os.path.join(ROOT, "speedreader", "__main__.py")], datas=[(speedreader/assets, "speedreader/assets")], excludes=["PySide6.QtQml","PySide6.QtQuick","PySide6.QtQuickWidgets","PySide6.QtNetwork","PySide6.QtOpenGL","PySide6.QtOpenGLWidgets","PySide6.QtSql","PySide6.QtTest","PySide6.QtXml","PySide6.QtConcurrent","PySide6.QtPrintSupport","PySide6.QtSvg","PySide6.QtSvgWidgets","tkinter","unittest","pydoc"])`; режим из `os.environ.get("SPEEDREADER_ONEFILE") == "1"`; `name="SpeedReader"`, `console=False`, `icon=assets/icon.ico` на Windows. Локальная проверка: `.venv/bin/pyinstaller packaging/speedreader.spec --noconfirm && QT_QPA_PLATFORM=offscreen dist/SpeedReader/SpeedReader --smoke; echo $?` → `0`; размер `du -sh dist/SpeedReader`.
- [ ] **Step 6: `packaging/build_deb.sh <version> <dist-dir> <out-dir>`** — `set -euo pipefail`; дерево `/opt/speedreader` ← `dist/SpeedReader`, `/usr/bin/speedreader` → симлинк, `/usr/share/applications/speedreader.desktop`, `/usr/share/icons/hicolor/256x256/apps/speedreader.png`; `DEBIAN/control`: `Package: speedreader`, `Version`, `Section: text`, `Priority: optional`, `Architecture: amd64`, `Maintainer: SpeedReader <speedreader@example.invalid>`, `Depends: libxcb-cursor0, libxkbcommon0, libgl1, libegl1, libfontconfig1, libdbus-1-3, libxcb-xinerama0`, `Description: Reader with a running word highlight for loveread.ec books`; `dpkg-deb --build --root-owner-group`. Локальная проверка: собрать, `dpkg -c out/*.deb | grep -c opt/speedreader` > 0, `dpkg -I out/*.deb` показывает Depends; если есть права — `sudo apt install ./out/*.deb && speedreader --smoke` (иначе отметить как непроверенное).
- [ ] **Step 7: `.github/workflows/build.yml`** — `on: push: tags: ["v*"]; workflow_dispatch:`. Jobs: `test` (ubuntu-22.04, `actions/setup-python@v5` 3.12, `sudo apt-get install -y libegl1 libxkbcommon0 libxcb-cursor0 libgl1 libdbus-1-3 libfontconfig1 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0`, `pip install -e . pyinstaller`, `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`); `build-windows` (needs test, windows-latest, `python packaging/set_version.py "$env:GITHUB_REF_NAME"` при теге иначе `0.0.0.dev$env:GITHUB_RUN_NUMBER`, `$env:SPEEDREADER_ONEFILE="1"; pyinstaller packaging/speedreader.spec --noconfirm`, `dist\SpeedReader.exe --smoke`, `actions/upload-artifact@v4` `SpeedReader.exe`); `build-linux` (needs test, ubuntu-22.04, те же apt-пакеты, pyinstaller onedir, `--smoke` под offscreen, `bash packaging/build_deb.sh "$DEB_VERSION" dist/SpeedReader out`, upload `out/*.deb`); `release` (needs обе сборки, `if: startsWith(github.ref, 'refs/tags/v')`, `actions/download-artifact@v4`, `softprops/action-gh-release@v2` с `files: SpeedReader.exe, *.deb`, `prerelease: ${{ needs.build-linux.outputs.prerelease }}`, `generate_release_notes: true`, `body` со строкой про SmartScreen: «Windows: при первом запуске нажмите „Подробнее“ → „Выполнить в любом случае“»). `permissions: contents: write`.
- [ ] **Step 8: README** — «Установка»: ссылки на релизы, deb через `sudo apt install ./speedreader_*.deb`, exe запускается напрямую, где лежат данные (`~/.local/share/SpeedReader`, `%APPDATA%\SpeedReader`); «Сборка»: локальные команды из шагов 5–6; раздел про парсер сохранить.
- [ ] **Step 9: Run** — `tests/test_packaging.py` PASS; весь набор OK; локальная сборка onedir и deb из шагов 5–6 выполнены, результаты записаны в сообщение коммита (размер папки, размер deb).
- [ ] **Step 10: Commit** `build: icons, pyinstaller spec, deb script and github actions workflow`

---

## Что проверяется только после пуша в GitHub

Windows-сборка и её `--smoke`, публикация релиза. Первый тег ставит пользователь; если `build-windows` падает, правится по логу задачи.
