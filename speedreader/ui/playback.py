from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from speedreader.library import LibraryError, LibraryStore
from speedreader.reading import ReadingModel, Word
from speedreader.ui.ticker import TickerView

PARAGRAPH_MARK = " ¶"


class PlaybackPage(QWidget):
    """Plays a book word by word on `ticker` and keeps the reading position in the store.

    Subclasses give the speed (`wpm`) and the error sink (`report_error`), and may
    react to play and pause or update their own controls in `refresh_controls`.
    """

    def __init__(self, store: LibraryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.key: str | None = None
        self.book: dict = {"blocks": []}
        self.model = ReadingModel(self.book)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self.ticker = TickerView()

    def wpm(self) -> int:
        raise NotImplementedError

    def report_error(self, message: str) -> None:
        raise NotImplementedError

    def on_play(self) -> None:
        pass

    def on_pause(self) -> None:
        pass

    def refresh_controls(self) -> None:
        pass

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
        self.refresh_controls()
        words = self.model.words
        pos = self.model.pos
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
            text += PARAGRAPH_MARK
        return text, block.get("type") == "heading"

    def current_delay_ms(self) -> int:
        return round(self.model.delay(self.wpm()) * 1000)

    def play(self) -> None:
        if self.playing or self.model.pos + 1 >= self.model.word_count:
            return
        self.on_play()
        self._timer.start(self.current_delay_ms())
        self.refresh()

    def pause(self, save: bool = True) -> None:
        self._timer.stop()
        self.on_pause()
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
            self.report_error(str(e))
            return None

    def save_position(self) -> None:
        if self.store.find(self.key) is not None and self.model.current is not None:
            self._write(self.store.set_position, self.key, *self.model.position, self.model.pos)

    def hideEvent(self, event) -> None:
        self.pause()
        super().hideEvent(event)
