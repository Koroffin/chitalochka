from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from speedreader.idle import IdleInhibitor
from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import ReadingModel, Word
from speedreader.ui.ticker import TickerView

_LABEL_LEN = 40
_PARAGRAPH_MARK = " ¶"


class ReaderPage(QWidget):
    back_requested = Signal()
    store_error = Signal(str)

    def __init__(
        self,
        store: LibraryStore,
        parent: QWidget | None = None,
        inhibitor: IdleInhibitor | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.inhibitor = inhibitor if inhibitor is not None else IdleInhibitor()
        self.key: str | None = None
        self.book: dict = {"blocks": []}
        self.model = ReadingModel(self.book)
        self.setFocusPolicy(Qt.StrongFocus)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

        self.ticker = TickerView()
        self.ticker.setMaximumWidth(960)
        self.ticker.clicked.connect(self.toggle)

        self.play_button = QToolButton()
        self.play_button.clicked.connect(self.toggle)
        self.wpm_slider = QSlider(Qt.Horizontal)
        self.wpm_slider.setRange(100, 1000)
        self.wpm_slider.setSingleStep(10)
        self.wpm_slider.setPageStep(50)
        self.wpm_slider.setValue(store.wpm)
        self.wpm_label = QLabel()
        self.wpm_slider.valueChanged.connect(self._on_wpm_changed)
        self.bookmark_button = QPushButton("Закладка")
        self.bookmark_button.setToolTip("Ctrl+B")
        self.bookmark_button.clicked.connect(self.add_bookmark)
        self.bookmarks_box = QComboBox()
        self.bookmarks_box.setPlaceholderText("Закладки")
        self.bookmarks_box.activated.connect(self._on_bookmark_activated)
        self.bookmarks_box.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bookmarks_box.customContextMenuRequested.connect(self._bookmark_menu)
        self.back_button = QPushButton("В библиотеку")
        self.back_button.clicked.connect(self.back_requested)
        self.status_label = QLabel()

        bar = QHBoxLayout()
        for widget in (
            self.play_button,
            self.wpm_slider,
            self.wpm_label,
            self.bookmark_button,
            self.bookmarks_box,
            self.back_button,
            self.status_label,
        ):
            # Keys belong to the page; a focused slider or combo would eat arrows.
            widget.setFocusPolicy(Qt.NoFocus)
            bar.addWidget(widget)
        column = QHBoxLayout()
        column.addStretch()
        column.addWidget(self.ticker, 1)
        column.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(column, 1)
        layout.addLayout(bar)
        self._on_wpm_changed(store.wpm, persist=False)
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
        self._fill_bookmarks()
        self.refresh()

    def refresh(self) -> None:
        words = self.model.words
        pos = self.model.pos
        self.play_button.setText("Пауза" if self.playing else "Старт")
        self.status_label.setText(f"{round(self.model.progress * 100)} %")
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
        return round(self.model.delay(self.store.wpm) * 1000)

    def play(self) -> None:
        if self.playing or self.model.pos + 1 >= self.model.word_count:
            return
        self.inhibitor.acquire()
        self._timer.start(self.current_delay_ms())
        self.refresh()

    def pause(self, save: bool = True) -> None:
        self._timer.stop()
        self.inhibitor.release()
        self.refresh()
        if save:
            self.save_position()

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def _tick(self) -> None:
        if not self.model.advance():
            self.pause()
            return
        self._timer.start(self.current_delay_ms())
        self.refresh()

    def _write(self, method, *args):
        try:
            return method(*args)
        except LibraryError as e:
            self.store_error.emit(str(e))
            return None

    def save_position(self) -> None:
        if self.store.find(self.key) is not None and self.model.current is not None:
            self._write(
                self.store.set_position, self.key, *self.model.position, self.model.pos
            )

    def _moved(self) -> None:
        self.refresh()
        self.save_position()

    def add_bookmark(self) -> None:
        word = self.model.current
        if self.key is None or word is None:
            return
        text = self.book["blocks"][word.block]["text"]
        label = text[word.start:word.start + _LABEL_LEN]
        if self._write(self.store.add_bookmark, self.key, *word.position, label):
            self._fill_bookmarks()

    def _fill_bookmarks(self) -> None:
        self.bookmarks_box.clear()
        for mark in self.store.find(self.key)["bookmarks"]:
            self.bookmarks_box.addItem(mark["label"], (mark["block"], mark["word"]))
        self.bookmarks_box.setCurrentIndex(-1)

    def _on_bookmark_activated(self, index: int) -> None:
        self.model.set_position(*self.bookmarks_box.itemData(index))
        self._moved()

    def _bookmark_menu(self, point) -> None:
        index = self.bookmarks_box.currentIndex()
        if index < 0:
            return
        menu = QMenu(self)
        remove = menu.addAction("Удалить")
        if menu.exec(self.bookmarks_box.mapToGlobal(point)) is remove:
            self._write(self.store.remove_bookmark, self.key, index)
            self._fill_bookmarks()

    def _on_wpm_changed(self, value: int, persist: bool = True) -> None:
        self.wpm_label.setText(f"{value} сл/мин")
        if persist:
            self._write(setattr, self.store, "wpm", value)

    def _handle_key(self, event) -> bool:
        key = event.key()
        if key == Qt.Key_B and event.modifiers() & Qt.ControlModifier:
            self.add_bookmark()
            return True
        navigation = {
            Qt.Key_Left: self.model.prev_sentence,
            Qt.Key_Right: self.model.next_sentence,
            Qt.Key_Up: self.model.prev_block,
            Qt.Key_Down: self.model.next_block,
        }
        if key in navigation:
            navigation[key]()
            self._moved()
        elif key == Qt.Key_Space:
            self.toggle()
        elif key == Qt.Key_Escape:
            self.back_requested.emit()
        else:
            return False
        return True

    def keyPressEvent(self, event) -> None:
        if not self._handle_key(event):
            super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self.pause()
        super().hideEvent(event)
