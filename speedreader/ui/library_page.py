from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from speedreader.library import LibraryError, LibraryStore

_KEY_ROLE = Qt.UserRole
_AVAILABLE_ROLE = Qt.UserRole + 1


class LibraryPage(QWidget):
    open_requested = Signal(str, bool)
    add_requested = Signal()
    redownload_requested = Signal(str)
    store_error = Signal(str)

    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _item: self.continue_button.click())
        self.empty_label = QLabel("Библиотека пуста. Нажмите «Добавить».")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.continue_button = QPushButton("Продолжить")
        self.continue_button.clicked.connect(self._continue)
        self.restart_button = QPushButton("Начать заново")
        self.restart_button.clicked.connect(self._restart)
        self.redownload_button = QPushButton("Скачать заново")
        self.redownload_button.clicked.connect(self._redownload)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self._delete)
        self.add_button = QPushButton("Добавить")
        self.add_button.clicked.connect(self.add_requested)

        buttons = QHBoxLayout()
        for button in (
            self.continue_button,
            self.restart_button,
            self.redownload_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.add_button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Библиотека"))
        layout.addWidget(self.list)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)
        self.refresh()

    def _row_text(self, entry: dict) -> tuple[str, bool]:
        prefix = f"{entry['title']} — {entry['author']}"
        if not self.store.is_available(entry["key"]):
            return f"{prefix} — файл книги не найден", False
        flat = entry["position"].get("flat", 0)
        count = entry["word_count"]
        percent = min(round(100 * flat / count), 100) if count else 0
        return f"{prefix} — {percent} %", True

    def refresh(self) -> None:
        selected = self.current_key()
        self.list.clear()
        for entry in self.store.entries:
            text, available = self._row_text(entry)
            item = QListWidgetItem(text)
            item.setData(_KEY_ROLE, entry["key"])
            item.setData(_AVAILABLE_ROLE, available)
            self.list.addItem(item)
            if entry["key"] == selected:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.empty_label.setVisible(self.list.count() == 0)
        self._update_buttons()

    def current_key(self) -> str | None:
        item = self.list.currentItem()
        return item.data(_KEY_ROLE) if item is not None else None

    def _update_buttons(self) -> None:
        item = self.list.currentItem()
        has_item = item is not None
        available = has_item and bool(item.data(_AVAILABLE_ROLE))
        self.continue_button.setEnabled(available)
        self.restart_button.setEnabled(available)
        self.redownload_button.setEnabled(has_item)
        self.delete_button.setEnabled(has_item)

    def _continue(self) -> None:
        key = self.current_key()
        if key is not None and self.continue_button.isEnabled():
            self.open_requested.emit(key, False)

    def _restart(self) -> None:
        key = self.current_key()
        if key is None:
            return
        try:
            self.store.reset_position(key)
        except LibraryError as e:
            self.store_error.emit(str(e))
            return
        self.open_requested.emit(key, True)

    def _redownload(self) -> None:
        key = self.current_key()
        if key is not None:
            self.redownload_requested.emit(key)

    def _delete(self) -> None:
        key = self.current_key()
        if key is None:
            return
        answer = QMessageBox.question(self, "Удаление", "Удалить книгу?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove(key)
        except LibraryError as e:
            self.store_error.emit(str(e))
        self.refresh()
