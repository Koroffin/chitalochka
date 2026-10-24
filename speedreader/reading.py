import re
from dataclasses import dataclass

_CLOSERS = "»\"'”’)]"
_SENTENCE_END = ".!?…"
_CLAUSE_END = ",;:-–—"
PREPOSITIONS = frozenset(
    "в без до из к на по о от перед при через с у за над об под про для".split()
)


@dataclass(frozen=True)
class Word:
    block: int
    index: int
    start: int
    end: int
    text: str
    last_in_block: bool

    @property
    def position(self) -> tuple[int, int]:
        return (self.block, self.index)


def split_words(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pending_start = None
    for m in re.finditer(r"\S+", text):
        if re.search(r"[^\W_]", m.group()):
            start = m.start() if pending_start is None else pending_start
            pending_start = None
            spans.append((start, m.end()))
        elif spans:
            spans[-1] = (spans[-1][0], m.end())
        elif pending_start is None:
            pending_start = m.start()
    return _glue_prepositions(text, spans)


def _is_bare_preposition(token: str) -> bool:
    if not token or not token[-1].isalnum():
        return False
    return re.sub(r"[\W_]", "", token).lower() in PREPOSITIONS


def _glue_prepositions(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(spans):
        start, end = spans[i]
        while i + 1 < len(spans) and _is_bare_preposition(text[spans[i][0]:spans[i][1]]):
            i += 1
            end = spans[i][1]
        out.append((start, end))
        i += 1
    return out


def _last_char(text: str) -> str:
    text = text.rstrip(_CLOSERS)
    return text[-1] if text else ""


def ends_sentence(text: str) -> bool:
    return _last_char(text) in _SENTENCE_END and _last_char(text) != ""


def ends_clause(text: str) -> bool:
    return _last_char(text) in _CLAUSE_END and _last_char(text) != ""


MIN_WEIGHT = 0.5
MAX_WEIGHT = 3.0


def word_length(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


def length_weights(lengths: list[int], chunk: int) -> list[float]:
    """Per-word time weights: length over the chunk average, clamped, then
    rescaled so every chunk still averages 1.0."""
    chunk = max(1, chunk)
    out: list[float] = []
    for i in range(0, len(lengths), chunk):
        part = lengths[i:i + chunk]
        avg = sum(part) / len(part)
        if avg <= 0:
            out.extend(1.0 for _ in part)
            continue
        raw = [min(MAX_WEIGHT, max(MIN_WEIGHT, n / avg)) for n in part]
        scale = len(raw) / sum(raw)
        out.extend(w * scale for w in raw)
    return out


def delay_for(word: Word, wpm: int, weight: float = 1.0) -> float:
    multiplier = 1.0
    if ends_clause(word.text):
        multiplier = max(multiplier, 1.5)
    if ends_sentence(word.text):
        multiplier = max(multiplier, 2.0)
    if word.last_in_block:
        multiplier = max(multiplier, 2.5)
    return 60 / wpm * weight * multiplier


class ReadingModel:
    def __init__(self, book: dict):
        self.words: list[Word] = []
        self._block_first: dict[int, int] = {}
        self._block_len: list[int] = []
        for b, block in enumerate(book.get("blocks", [])):
            text = block["text"]
            spans = split_words(text)
            self._block_len.append(len(spans))
            if spans:
                self._block_first[b] = len(self.words)
            for i, (s, e) in enumerate(spans):
                self.words.append(
                    Word(b, i, s, e, text[s:e], i == len(spans) - 1)
                )
        self.word_count = len(self.words)
        self.pos = 0
        self._lengths = [word_length(w.text) for w in self.words]
        self._weights_wpm = 0
        self._weights: list[float] = []

    @property
    def current(self) -> Word | None:
        return self.words[self.pos] if self.words else None

    @property
    def position(self) -> tuple[int, int]:
        word = self.current
        return word.position if word else (0, 0)

    def flat_index(self, block: int, word: int) -> int:
        if not self.words:
            return 0
        block = max(0, min(block, len(self._block_len) - 1))
        count = self._block_len[block]
        if count == 0:
            # Block without words: land on the next word that exists.
            return min(
                (i for b, i in self._block_first.items() if b > block),
                default=self.word_count - 1,
            )
        word = max(0, min(word, count - 1))
        return self._block_first[block] + word

    def set_position(self, block: int, word: int) -> None:
        self.pos = self.flat_index(block, word)

    def advance(self) -> bool:
        if self.pos + 1 >= self.word_count:
            return False
        self.pos += 1
        return True

    def weights(self, wpm: int) -> list[float]:
        if wpm != self._weights_wpm:
            self._weights = length_weights(self._lengths, wpm)
            self._weights_wpm = wpm
        return self._weights

    def delay(self, wpm: int) -> float:
        word = self.current
        if not word:
            return 60 / wpm
        return delay_for(word, wpm, self.weights(wpm)[self.pos])

    def is_sentence_start(self, i: int) -> bool:
        return (
            i == 0
            or self.words[i - 1].last_in_block
            or ends_sentence(self.words[i - 1].text)
        )

    def prev_sentence(self) -> None:
        i = self.pos
        while i > 0:
            i -= 1
            if self.is_sentence_start(i):
                break
        self.pos = i

    def next_sentence(self) -> None:
        for i in range(self.pos + 1, self.word_count):
            if self.is_sentence_start(i):
                self.pos = i
                return
        self.pos = max(self.word_count - 1, 0)

    def prev_block(self) -> None:
        if not self.words:
            return
        block = self.words[self.pos].block
        if self.pos > self._block_first[block]:
            self.pos = self._block_first[block]
            return
        earlier = [i for b, i in self._block_first.items() if b < block]
        if earlier:
            self.pos = max(earlier)

    def next_block(self) -> None:
        if not self.words:
            return
        block = self.words[self.pos].block
        later = [i for b, i in self._block_first.items() if b > block]
        if later:
            self.pos = min(later)

    @property
    def progress(self) -> float:
        return self.pos / self.word_count if self.word_count else 0.0
