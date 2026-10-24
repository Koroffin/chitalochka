from bisect import bisect_right

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import ReadingModel

_HIGHLIGHT = QColor("#ffe58a")
_LABEL_LEN = 40


def _utf16_len(text: str) -> int:
    # Qt document positions count UTF-16 units; emoji take two.
    return len(text.encode("utf-16-le")) // 2


class ReaderPage(QWidget):
    back_requested = Signal()
    store_error = Signal(str)

    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.key: str | None = None
        self.book: dict = {"blocks": []}
        self.model = ReadingModel(self.book)
        self.word_offsets: list[int] = []
        self.setFocusPolicy(Qt.StrongFocus)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

        self.text = QTextEdit(readOnly=True)
        self.text.setUndoRedoEnabled(False)
        self.text.setMaximumWidth(760)
        self.text.document().setDocumentMargin(24)
        font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        font.setFamily("Serif")
        font.setStyleHint(QFont.Serif)
        font.setPointSize(16)
        self.text.document().setDefaultFont(font)
        self._viewport = self.text.viewport()
        self.text.installEventFilter(self)
        self._viewport.installEventFilter(self)
        self.text.verticalScrollBar().sliderPressed.connect(self.on_user_scrolled)

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
        column.addWidget(self.text, 1)
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
        self._build_document()
        self._fill_bookmarks()
        self.refresh()

    def _build_document(self) -> None:
        doc = self.text.document()
        doc.clear()
        base = doc.defaultFont()
        plain = QTextCharFormat()
        plain.setFont(base)
        heading = QTextCharFormat()
        heading.setFont(base)
        heading.setFontWeight(QFont.Bold)
        heading.setFontPointSize(base.pointSizeF() * 1.3)
        indented = QTextBlockFormat()
        indented.setTextIndent(24)
        flat = QTextBlockFormat()

        self.word_offsets = []
        words = self.model.words
        i = 0
        cursor = QTextCursor(doc)
        # One edit block: otherwise a visible widget relayouts after every insert.
        cursor.beginEditBlock()
        for b, block in enumerate(self.book.get("blocks", [])):
            is_heading = block["type"] == "heading"
            block_format = flat if is_heading else indented
            if b == 0:
                cursor.setBlockFormat(block_format)
            else:
                cursor.insertBlock(block_format)
            block_start = cursor.position()
            text = block["text"]
            cursor.insertText(text, heading if is_heading else plain)
            while i < len(words) and words[i].block == b:
                self.word_offsets.append(block_start + _utf16_len(text[:words[i].start]))
                i += 1
        cursor.endEditBlock()

    def refresh(self) -> None:
        word = self.model.current
        self.play_button.setText("Пауза" if self.playing else "Старт")
        self.status_label.setText(f"{round(self.model.progress * 100)} %")
        if word is None:
            self.text.setExtraSelections([])
            return
        start = self.word_offsets[self.model.pos]
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(_HIGHLIGHT)
        selection.cursor = QTextCursor(self.text.document())
        selection.cursor.setPosition(start)
        selection.cursor.setPosition(start + _utf16_len(word.text), QTextCursor.KeepAnchor)
        self.text.setExtraSelections([selection])

        cursor = QTextCursor(self.text.document())
        cursor.setPosition(start)
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()
        if self.text.cursorRect().bottom() > self._viewport.height() * 2 / 3:
            self._center_cursor()

    def _center_cursor(self) -> None:
        # QTextEdit has no centerCursor(); only QPlainTextEdit does.
        bar = self.text.verticalScrollBar()
        offset = self.text.cursorRect().center().y() - self._viewport.height() // 2
        bar.setValue(bar.value() + offset)

    def current_delay_ms(self) -> int:
        return round(self.model.delay(self.store.wpm) * 1000)

    def play(self) -> None:
        if self.playing or self.model.pos + 1 >= self.model.word_count:
            return
        self._timer.start(self.current_delay_ms())
        self.refresh()
        self._center_cursor()

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

    def jump_to_document_position(self, pos: int) -> None:
        if not self.word_offsets:
            return
        idx = max(bisect_right(self.word_offsets, pos) - 1, 0)
        self.model.set_position(*self.model.words[idx].position)
        self._moved()

    def on_user_scrolled(self) -> None:
        if self.playing:
            self.pause()

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

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.text and event.type() == QEvent.KeyPress:
            return self._handle_key(event)
        if obj is self._viewport:
            if event.type() == QEvent.Wheel:
                self.on_user_scrolled()
            elif event.type() == QEvent.MouseButtonPress:
                pos = self.text.cursorForPosition(event.position().toPoint()).position()
                self.jump_to_document_position(pos)
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:
        self.pause()
        super().hideEvent(event)
