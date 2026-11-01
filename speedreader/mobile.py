import argparse
import os
import sys
from collections.abc import Callable, MutableMapping
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from speedreader.app import default_data_dir, exit_code
from speedreader.idle import is_android
from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import ReadingModel, Word
from speedreader.sources import detect
from speedreader.ui.add_page import AddPage
from speedreader.ui.library_page import (
    _AVAILABLE_ROLE,
    _KEY_ROLE,
    EMPTY_TEXT,
    row_text,
)
from speedreader.ui.reader_page import _PARAGRAPH_MARK
from speedreader.ui.ticker import TickerView

ANDROID_WPM = 200
MIN_TOUCH_PX = 48
LONG_PRESS_MS = 500
DRAG_CANCEL_PX = 20
PROGRESS_COLOR = QColor("#c9a227")


def classify_gesture(dx: float, dy: float, width: float) -> str:
    if abs(dx) < DRAG_CANCEL_PX and abs(dy) < DRAG_CANCEL_PX:
        return "tap"
    if dx >= width / 5 and abs(dx) > 2 * abs(dy):
        return "rewind"
    return "none"


class MobileLibraryPage(QWidget):
    open_requested = Signal(str, bool)
    add_requested = Signal()
    redownload_requested = Signal(str)

    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store

        self.add_button = QPushButton("Добавить")
        self.add_button.setMinimumHeight(MIN_TOUCH_PX)
        self.add_button.clicked.connect(self.add_requested)
        self.list = QListWidget()
        self.list.setStyleSheet(f"QListWidget::item {{ min-height: {MIN_TOUCH_PX}px; }}")
        self.list.itemClicked.connect(self._open)
        self.list.viewport().installEventFilter(self)
        self.long_press_timer = QTimer(self)
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.setInterval(LONG_PRESS_MS)
        self.long_press_timer.timeout.connect(self._long_press_fired)
        self._press_pos = QPoint()
        self._long_pressed = False
        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setAlignment(Qt.AlignCenter)

        header = QHBoxLayout()
        header.addWidget(QLabel("Библиотека"))
        header.addStretch()
        header.addWidget(self.add_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.list)
        layout.addWidget(self.empty_label)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for entry in self.store.entries:
            text, available = row_text(self.store, entry)
            item = QListWidgetItem(text)
            item.setData(_KEY_ROLE, entry["key"])
            item.setData(_AVAILABLE_ROLE, available)
            self.list.addItem(item)
        self.empty_label.setVisible(self.list.count() == 0)

    def key_at(self, item: QListWidgetItem) -> str:
        return item.data(_KEY_ROLE)

    def item_text(self, key: str) -> str:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if self.key_at(item) == key:
                return item.text()
        raise KeyError(key)

    def _open(self, item: QListWidgetItem) -> None:
        # The release that ends a long press must not also open the book.
        if not self._long_pressed and item.data(_AVAILABLE_ROLE):
            self.open_requested.emit(self.key_at(item), False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.list.viewport():
            kind = event.type()
            if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._long_pressed = False
                self._press_pos = event.position().toPoint()
                if self.list.itemAt(self._press_pos) is not None:
                    self.long_press_timer.start()
            elif kind == QEvent.MouseMove:
                moved = event.position().toPoint() - self._press_pos
                if moved.manhattanLength() > DRAG_CANCEL_PX:
                    self.long_press_timer.stop()
            elif kind == QEvent.MouseButtonRelease:
                self.long_press_timer.stop()
        return super().eventFilter(watched, event)

    def _long_press_fired(self) -> None:
        item = self.list.itemAt(self._press_pos)
        if item is None:
            return
        self._long_pressed = True
        self.show_menu(item, self.list.viewport().mapToGlobal(self._press_pos))

    def menu_actions(self, key: str) -> list[str]:
        actions = []
        if self.store.is_available(key):
            actions.append("Начать заново")
        entry = self.store.find(key)
        if entry is not None and detect(entry["source_url"]) is not None:
            actions.append("Скачать заново")
        actions.append("Удалить")
        return actions

    def show_menu(self, item: QListWidgetItem, global_pos: QPoint) -> None:
        key = self.key_at(item)
        menu = QMenu(self)
        for text in self.menu_actions(key):
            menu.addAction(text)
        chosen = menu.exec(global_pos)
        if chosen is not None:
            self.run_action(key, chosen.text())

    def run_action(self, key: str, text: str) -> None:
        if text == "Начать заново":
            try:
                self.store.reset_position(key)
            except LibraryError as e:
                self._warn(str(e))
                return
            self.open_requested.emit(key, True)
        elif text == "Скачать заново":
            self.redownload_requested.emit(key)
        elif text == "Удалить":
            answer = QMessageBox.question(self, "Удаление", "Удалить книгу?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.remove(key)
            except LibraryError as e:
                self._warn(str(e))
            self.refresh()

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "SpeedReader", f"Ошибка хранилища: {message}")


class ProgressStrip(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fraction = 0.0
        self.setFixedHeight(4)

    def set_fraction(self, value: float) -> None:
        self.fraction = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().mid())
        painter.fillRect(QRectF(0, 0, self.fraction * self.width(), self.height()), PROGRESS_COLOR)


class MobileReaderPage(QWidget):
    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.key: str | None = None
        self.book: dict = {"blocks": []}
        self.model = ReadingModel(self.book)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

        self.strip = ProgressStrip()
        self.ticker = TickerView()
        self.ticker.installEventFilter(self)
        self._press_pos = QPoint()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.strip)
        layout.addWidget(self.ticker, 1)
        self.refresh()

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    def load(self, key: str, book: dict, restart: bool = False) -> None:
        # The previous book is saved on hide; saving here would undo a restart's reset.
        self.pause(save=False)
        self.key = key
        self.book = book
        self.model = ReadingModel(book)
        if not restart:
            pos = self.store.find(key)["position"]
            self.model.set_position(pos["block"], pos["word"])
        self.refresh()

    def refresh(self) -> None:
        words = self.model.words
        pos = self.model.pos
        self.strip.set_fraction(self.model.progress)
        if not words:
            self.ticker.set_words("", "", "")
            return
        slots = [
            self._slot(words[pos - 1]) if pos > 0 else ("", False),
            self._slot(words[pos]),
            self._slot(words[pos + 1]) if pos + 1 < len(words) else ("", False),
        ]
        self.ticker.set_words(*(text for text, _ in slots), bold=tuple(b for _, b in slots))

    def _slot(self, word: Word) -> tuple[str, bool]:
        block = self.book["blocks"][word.block]
        text = word.text
        if word.last_in_block and word.block + 1 < len(self.book["blocks"]):
            text += _PARAGRAPH_MARK
        return text, block.get("type") == "heading"

    def current_delay_ms(self) -> int:
        return round(self.model.delay(ANDROID_WPM) * 1000)

    def play(self) -> None:
        if self.playing or self.model.pos + 1 >= self.model.word_count:
            return
        self._timer.start(self.current_delay_ms())
        self.refresh()

    def pause(self, save: bool = True) -> None:
        self._timer.stop()
        self.refresh()
        if save:
            self.save_position()

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def rewind(self) -> None:
        self.model.prev_sentence()
        self.refresh()
        if self.playing:
            self._timer.start(self.current_delay_ms())
        else:
            self.save_position()

    def _tick(self) -> None:
        if not self.model.advance():
            self.pause()
            return
        self._timer.start(self.current_delay_ms())
        self.refresh()

    def save_position(self) -> None:
        if self.store.find(self.key) is None or self.model.current is None:
            return
        try:
            self.store.set_position(self.key, *self.model.position, self.model.pos)
        except LibraryError as e:
            QMessageBox.warning(self, "SpeedReader", f"Ошибка хранилища: {e}")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.ticker:
            kind = event.type()
            if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._press_pos = event.position().toPoint()
            elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                moved = event.position().toPoint() - self._press_pos
                # The ticker spans the page; its own width is a 640 px default until first shown.
                gesture = classify_gesture(moved.x(), moved.y(), self.width())
                if gesture == "tap":
                    self.toggle()
                elif gesture == "rewind":
                    self.rewind()
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self.pause()
        super().hideEvent(event)


class MobileWindow(QStackedWidget):
    def __init__(self, store: LibraryStore):
        super().__init__()
        self.store = store
        self.setWindowTitle("SpeedReader")
        self.library_page = MobileLibraryPage(store)
        self.add_page = AddPage(store, mobile=True)
        self.reader_page = MobileReaderPage(store)
        for page in (self.library_page, self.add_page, self.reader_page):
            self.addWidget(page)

        self.library_page.open_requested.connect(self.open_book)
        self.library_page.add_requested.connect(self._show_add)
        self.library_page.redownload_requested.connect(self._redownload)
        self.add_page.book_ready.connect(lambda key: self.open_book(key, False))
        self.add_page.back_requested.connect(self.show_library)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self.show_library()

    def show_library(self) -> None:
        self.library_page.refresh()
        self.setCurrentWidget(self.library_page)

    def open_book(self, key: str, restart: bool) -> None:
        try:
            book = self.store.load_book(key)
            self.reader_page.load(key, book, restart)
        except LibraryError as e:
            QMessageBox.warning(self, "SpeedReader", f"Ошибка хранилища: {e}")
            self.show_library()
            return
        self.setCurrentWidget(self.reader_page)
        self.reader_page.setFocus()

    def back_from_reader(self) -> None:
        self.reader_page.save_position()
        self.show_library()

    def _show_add(self) -> None:
        self.add_page.error_label.clear()
        self.setCurrentWidget(self.add_page)
        self.add_page.url_edit.setFocus()

    def _redownload(self, key: str) -> None:
        entry = self.store.find(key)
        if entry is None:
            return
        self.add_page.url_edit.setText(entry["source_url"])
        self.setCurrentWidget(self.add_page)
        self.add_page.start_download(force=True)

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationActive:
            self.reader_page.pause()

    def keyPressEvent(self, event) -> None:
        if event.key() != Qt.Key_Back:
            super().keyPressEvent(event)
        elif self.currentWidget() is self.reader_page:
            self.back_from_reader()
        elif self.currentWidget() is self.add_page:
            self.add_page.go_back()
        else:
            # Unhandled Back reaches Android, which closes the activity.
            event.ignore()


def prepare_environment(
    environ: MutableMapping[str, str], android: bool, cert_path: Callable[[], str]
) -> None:
    if android and "SSL_CERT_FILE" not in environ:
        environ["SSL_CERT_FILE"] = cert_path()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="speedreader")
    parser.add_argument("--smoke", action="store_true", help="открыть окно и сразу выйти")
    parser.add_argument("--data-dir", type=Path, help="папка с библиотекой")
    args = parser.parse_args(argv)

    # OpenSSL in the APK finds no CA bundle on Android; certifi ships one.
    prepare_environment(os.environ, is_android(), lambda: __import__("certifi").where())
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("SpeedReader")
    data_dir = args.data_dir or default_data_dir()
    try:
        store = LibraryStore(data_dir)
    except LibraryError as e:
        message = f"Ошибка хранилища: {e}"
        print(message, file=sys.stderr)
        QMessageBox.critical(None, "SpeedReader", message)
        return 1

    window = MobileWindow(store)
    window.showMaximized()
    if args.smoke:
        QTimer.singleShot(0, app.quit)
    return exit_code(app.exec())
