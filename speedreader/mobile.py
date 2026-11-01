from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from speedreader.library import LibraryError, LibraryStore
from speedreader.sources import detect
from speedreader.ui.library_page import (
    _AVAILABLE_ROLE,
    _KEY_ROLE,
    EMPTY_TEXT,
    row_text,
)

ANDROID_WPM = 200
MIN_TOUCH_PX = 48
LONG_PRESS_MS = 500
DRAG_CANCEL_PX = 20


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
