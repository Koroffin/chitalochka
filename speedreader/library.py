import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from speedreader.reading import ReadingModel

_VERSION = 1
_DEFAULT_WPM = 250


class LibraryError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_atomic(path: Path, data: dict) -> None:
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as tmp:
            tmp_name = tmp.name
            json.dump(data, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except OSError as exc:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        raise LibraryError(f"Cannot write {path}: {exc}") from exc


class LibraryStore:
    def __init__(self, data_dir: Path):
        self._dir = Path(data_dir)
        self._books_dir = self._dir / "books"
        self._index = self._dir / "library.json"
        self._wpm = _DEFAULT_WPM
        self._entries: list[dict] = []
        try:
            self._books_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LibraryError(f"Cannot create {self._books_dir}: {exc}") from exc
        self._load()

    @property
    def entries(self) -> list[dict]:
        return self._entries

    @property
    def wpm(self) -> int:
        return self._wpm

    @wpm.setter
    def wpm(self, value: int) -> None:
        self._wpm = int(value)
        self.save()

    def _load(self) -> None:
        if not self._index.exists():
            return
        try:
            data = json.loads(self._index.read_text(encoding="utf-8"))
            books = data["books"]
            wpm = int(data.get("wpm", _DEFAULT_WPM))
            if not isinstance(books, list) or not all(isinstance(b, dict) for b in books):
                raise ValueError("books must be a list of objects")
        except (ValueError, KeyError, TypeError, AttributeError):
            self._quarantine()
            return
        except OSError as exc:
            raise LibraryError(f"Cannot read {self._index}: {exc}") from exc
        self._wpm = wpm
        self._entries = books

    def _quarantine(self) -> None:
        try:
            os.replace(self._index, self._index.with_name("library.json.broken"))
        except OSError as exc:
            raise LibraryError(f"Cannot move aside {self._index}: {exc}") from exc

    def save(self) -> None:
        _write_atomic(
            self._index,
            {"version": _VERSION, "wpm": self._wpm, "books": self._entries},
        )

    def find(self, key: str) -> dict | None:
        for entry in self._entries:
            if entry["key"] == key:
                return entry
        return None

    def _get(self, key: str) -> dict:
        entry = self.find(key)
        if entry is None:
            raise LibraryError(f"No such book: {key}")
        return entry

    def _book_path(self, key: str) -> Path:
        return self._books_dir / f"{key}.json"

    def add_book(self, key: str, book: dict) -> dict:
        model = ReadingModel(book)
        _write_atomic(self._book_path(key), book)
        entry = self.find(key)
        fields = {
            "title": book["title"],
            "author": book["author"],
            "source_url": book["source"],
            "complete": book["complete"],
            "word_count": model.word_count,
        }
        if entry is None:
            entry = {
                "key": key,
                **fields,
                "added_at": _now(),
                "position": {"block": 0, "word": 0},
                "bookmarks": [],
            }
            self._entries.insert(0, entry)
        else:
            entry.update(fields)
            entry["position"] = self._clamp(model, entry["position"])
            entry["bookmarks"] = [
                {**mark, **self._clamp(model, mark)} for mark in entry["bookmarks"]
            ]
        self.save()
        return entry

    @staticmethod
    def _clamp(model: ReadingModel, pos: dict) -> dict:
        model.set_position(pos["block"], pos["word"])
        block, word = model.position
        return {"block": block, "word": word}

    def load_book(self, key: str) -> dict:
        path = self._book_path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LibraryError(f"Cannot load book {key}: {exc}") from exc

    def is_available(self, key: str) -> bool:
        return self.find(key) is not None and self._book_path(key).is_file()

    def set_position(self, key: str, block: int, word: int) -> None:
        self._get(key)["position"] = {"block": block, "word": word}
        self.save()

    def reset_position(self, key: str) -> None:
        self.set_position(key, 0, 0)

    def add_bookmark(self, key: str, block: int, word: int, label: str) -> bool:
        entry = self._get(key)
        if any(m["block"] == block and m["word"] == word for m in entry["bookmarks"]):
            return False
        entry["bookmarks"].append(
            {"block": block, "word": word, "created_at": _now(), "label": label}
        )
        self.save()
        return True

    def remove_bookmark(self, key: str, index: int) -> None:
        del self._get(key)["bookmarks"][index]
        self.save()

    def remove(self, key: str) -> None:
        entry = self._get(key)
        try:
            self._book_path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise LibraryError(f"Cannot delete book {key}: {exc}") from exc
        self._entries.remove(entry)
        self.save()
