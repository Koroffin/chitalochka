from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

HIGHLIGHT = QColor("#ffe58a")
DIM_OPACITY = 0.4
_GAP = 40
_PAD = 10


class TickerView(QWidget):
    """One line: the current word group highlighted in the centre, the previous
    and the next ones dimmed at its sides."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.previous = ""
        self.current = ""
        self.following = ""
        font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        font.setFamily("Serif")
        font.setStyleHint(QFont.Serif)
        font.setPointSize(28)
        self.setFont(font)
        self.setMinimumHeight(QFontMetricsF(font).height() * 3)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(True)

    def set_words(self, previous: str, current: str, following: str) -> None:
        self.previous, self.current, self.following = previous, current, following
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        fm = QFontMetricsF(self.font())
        mid_x = self.width() / 2
        mid_y = self.height() / 2
        line_h = fm.height()
        top = mid_y - line_h / 2

        cur_w = fm.horizontalAdvance(self.current)
        cur_rect = QRectF(mid_x - cur_w / 2, top, cur_w, line_h)
        painter.fillRect(cur_rect.adjusted(-_PAD, -_PAD / 2, _PAD, _PAD / 2), HIGHLIGHT)
        painter.setPen(self.palette().text().color())
        painter.drawText(cur_rect, Qt.AlignCenter, self.current)

        painter.setOpacity(DIM_OPACITY)
        side_w = max(0.0, mid_x - cur_w / 2 - _GAP - _PAD)
        if self.previous and side_w > 0:
            text = fm.elidedText(self.previous, Qt.ElideLeft, side_w)
            rect = QRectF(cur_rect.left() - _GAP - side_w, top, side_w, line_h)
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)
        if self.following and side_w > 0:
            text = fm.elidedText(self.following, Qt.ElideRight, side_w)
            rect = QRectF(cur_rect.right() + _GAP, top, side_w, line_h)
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.end()
