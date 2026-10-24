import re

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from speedreader.library import LibraryError, LibraryStore
from speedreader.sources import SOURCES, SourceError, book_key, import_book_json, normalize_url

_PAGE_RE = re.compile(r"page (\d+)/(\d+)")
_CANCEL_TEXT = "Отмена"


class DownloadWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, source, url: str):
        super().__init__()
        self.source = source
        self.url = url
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _log(self, message: str) -> None:
        match = _PAGE_RE.search(message)
        if match:
            self.progress.emit(int(match.group(1)), int(match.group(2)))

    def run(self) -> None:
        try:
            book = self.source.download(
                self.url, log=self._log, should_stop=lambda: self._cancelled
            )
        except SourceError as e:
            text = f"Ошибка: {str(e).rstrip('.')}. Попробуйте ещё раз."
            if e.pages_fetched > 0:
                text = f"Скачано страниц: {e.pages_fetched}. {text}"
            self.failed.emit(text)
            return
        except Exception as e:
            self.failed.emit(f"Ошибка: {e}")
            return
        self.finished_ok.emit(book)


class AddPage(QWidget):
    book_ready = Signal(str)
    back_requested = Signal()

    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.sources: list = list(SOURCES)
        self._worker: DownloadWorker | None = None
        self._generation = 0
        self._key: str | None = None

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://loveread.ec/book-comments.php?book=36708")
        self.url_edit.returnPressed.connect(self.start_download)
        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(lambda: self.start_download())
        self.import_button = QPushButton("Импорт JSON")
        self.import_button.clicked.connect(self._choose_json)
        self.pdf_button = QPushButton("Загрузить PDF")
        self.pdf_button.setEnabled(False)
        self.pdf_button.setToolTip("Скоро: чтение из PDF")
        self.progress = QProgressBar()
        self.progress.hide()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.cancel_button = QPushButton(_CANCEL_TEXT)
        self.cancel_button.clicked.connect(self.request_cancel)
        self.cancel_button.hide()
        self.back_button = QPushButton("Назад")
        self.back_button.clicked.connect(self.back_requested)

        buttons = QHBoxLayout()
        for button in (self.download_button, self.import_button, self.pdf_button):
            buttons.addWidget(button)
        buttons.addStretch()
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.back_button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Ссылка на книгу (loveread.ec)"))
        layout.addWidget(self.url_edit)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.error_label)
        layout.addStretch()
        layout.addLayout(bottom)

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.download_button, self.import_button, self.back_button, self.url_edit):
            widget.setEnabled(not busy)
        self.cancel_button.setText(_CANCEL_TEXT)
        self.cancel_button.setEnabled(busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def start_download(self, force: bool = False) -> None:
        if self.busy:
            return
        self.error_label.clear()
        url = normalize_url(self.url_edit.text())
        source = next((s for s in self.sources if s.matches(url)), None)
        if source is None:
            self.error_label.setText("Пока поддерживается только loveread.ec")
            return
        key = source.key_for_url(url)
        if not force and self.store.find(key) is not None and self.store.is_available(key):
            self.book_ready.emit(key)
            return

        self._generation += 1
        gen = self._generation
        self._key = key
        self._worker = DownloadWorker(source, url)
        self._worker.progress.connect(
            lambda page, total: self._on_progress(gen, page, total), Qt.QueuedConnection
        )
        self._worker.finished_ok.connect(lambda book: self._on_done(gen, book), Qt.QueuedConnection)
        self._worker.failed.connect(lambda text: self._on_failed(gen, text), Qt.QueuedConnection)
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self._set_busy(True)
        self._worker.start()

    def _on_progress(self, gen: int, page: int, total: int) -> None:
        if gen != self._generation:
            return
        if self.progress.maximum() != total:
            self.progress.setRange(0, total)
        self.progress.setValue(page)

    def _finish(self) -> DownloadWorker:
        worker = self._worker
        # The signal comes right before run() returns; wait so the thread is dead when dropped.
        worker.wait()
        self._worker = None
        self._set_busy(False)
        return worker

    def _on_done(self, gen: int, book: dict) -> None:
        if gen != self._generation:
            return
        worker = self._finish()
        if worker._cancelled:
            self.error_label.setText("Отменено")
            return
        self._store_and_emit(self._key, book)

    def _on_failed(self, gen: int, text: str) -> None:
        if gen != self._generation:
            return
        worker = self._finish()
        self.error_label.setText("Отменено" if worker._cancelled else text)

    def _store_and_emit(self, key: str, book: dict) -> None:
        try:
            self.store.add_book(key, book)
        except LibraryError as e:
            self.error_label.setText(f"Не удалось сохранить книгу: {e}")
            return
        self.book_ready.emit(key)

    def request_cancel(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        self.cancel_button.setText("Отмена…")
        self.cancel_button.setEnabled(False)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        if self._worker is None:
            return
        self._generation += 1
        self._worker.cancel()
        if self._worker.wait(timeout_ms):
            self._worker = None
            self._set_busy(False)

    def _choose_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Импорт JSON", "", "JSON (*.json)")
        if path:
            self.import_json(path)

    def import_json(self, path: str) -> None:
        if self.busy:
            return
        self.error_label.clear()
        try:
            book = import_book_json(path)
        except SourceError as e:
            self.error_label.setText(str(e))
            return
        self._store_and_emit(book_key(book), book)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and not self.busy:
            self.back_button.click()
        else:
            super().keyPressEvent(event)
