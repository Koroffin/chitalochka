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

from speedreader.library import LibraryStore
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
        if item.data(_AVAILABLE_ROLE):
            self.open_requested.emit(self.key_at(item), False)

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "SpeedReader", f"Ошибка хранилища: {message}")
