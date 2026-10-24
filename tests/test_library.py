import json
import tempfile
import unittest
from pathlib import Path

from speedreader.library import LibraryError, LibraryStore


def make_book(blocks=None):
    if blocks is None:
        blocks = [
            {"type": "paragraph", "page": 1, "text": "Раз два. Три четыре!"},
            {"type": "paragraph", "page": 1, "text": "Пять шесть."},
        ]
    return {
        "id": 1,
        "title": "T",
        "author": "A",
        "source": "https://loveread.ec/book-comments.php?book=1",
        "pages": 1,
        "pages_fetched": 1,
        "complete": True,
        "blocks": blocks,
    }


class LibraryStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_add_book_writes_file_and_entry(self):
        store = LibraryStore(self.tmp)
        store.add_book("loveread-1", make_book())
        self.assertTrue((self.tmp / "books" / "loveread-1.json").exists())
        entry = store.entries[0]
        self.assertEqual(entry["word_count"], 6)
        self.assertEqual(entry["position"], {"block": 0, "word": 0})
        self.assertEqual(entry["source_url"], make_book()["source"])
        self.assertEqual(store.load_book("loveread-1")["title"], "T")

    def test_new_entries_go_first(self):
        store = LibraryStore(self.tmp)
        store.add_book("a", make_book())
        store.add_book("b", make_book())
        self.assertEqual([e["key"] for e in store.entries], ["b", "a"])

    def test_persists_between_instances(self):
        store = LibraryStore(self.tmp)
        store.add_book("loveread-1", make_book())
        store.wpm = 300
        other = LibraryStore(self.tmp)
        self.assertEqual(other.wpm, 300)
        self.assertEqual(other.find("loveread-1")["title"], "T")

    def test_default_wpm(self):
        self.assertEqual(LibraryStore(self.tmp).wpm, 250)

    def test_broken_library_json_is_renamed_and_store_starts_empty(self):
        (self.tmp / "library.json").write_text("{", encoding="utf-8")
        store = LibraryStore(self.tmp)
        self.assertEqual(store.entries, [])
        self.assertTrue((self.tmp / "library.json.broken").exists())

    def test_readd_keeps_position_and_clamps(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        store.set_position("k", 1, 1)
        store.add_bookmark("k", 1, 1, "label")
        short = make_book([{"type": "paragraph", "page": 1, "text": "Раз два"}])
        store.add_book("k", short)
        entry = store.find("k")
        self.assertEqual(len(store.entries), 1)
        self.assertEqual(entry["position"], {"block": 0, "word": 1, "flat": 1})
        self.assertEqual(entry["bookmarks"][0]["block"], 0)
        self.assertEqual(entry["bookmarks"][0]["word"], 1)
        self.assertEqual(entry["word_count"], 2)

    def test_missing_book_file(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        (self.tmp / "books" / "k.json").unlink()
        self.assertFalse(store.is_available("k"))
        with self.assertRaises(LibraryError):
            store.load_book("k")

    def test_invalid_book_file(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        (self.tmp / "books" / "k.json").write_text("{", encoding="utf-8")
        with self.assertRaises(LibraryError):
            store.load_book("k")

    def test_book_file_without_blocks_raises(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        for content in ("[]", '{"blocks": [{"type": "paragraph"}]}'):
            (self.tmp / "books" / "k.json").write_text(content, encoding="utf-8")
            with self.assertRaises(LibraryError):
                store.load_book("k")

    def test_position_flat_stored_and_reloaded(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        store.set_position("k", 1, 1, flat=5)
        self.assertEqual(
            LibraryStore(self.tmp).find("k")["position"], {"block": 1, "word": 1, "flat": 5}
        )

    def test_unknown_key_raises(self):
        store = LibraryStore(self.tmp)
        self.assertIsNone(store.find("x"))
        self.assertFalse(store.is_available("x"))
        with self.assertRaises(LibraryError):
            store.set_position("x", 0, 0)

    def test_bookmark_no_duplicates_and_remove(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        self.assertTrue(store.add_bookmark("k", 0, 2, "x"))
        self.assertFalse(store.add_bookmark("k", 0, 2, "y"))
        self.assertEqual(len(store.find("k")["bookmarks"]), 1)
        store.remove_bookmark("k", 0)
        self.assertEqual(store.find("k")["bookmarks"], [])
        with self.assertRaises(IndexError):
            store.remove_bookmark("k", 0)

    def test_reset_position_keeps_bookmarks(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        store.set_position("k", 1, 1)
        store.add_bookmark("k", 1, 0, "x")
        store.reset_position("k")
        entry = LibraryStore(self.tmp).find("k")
        self.assertEqual(entry["position"], {"block": 0, "word": 0})
        self.assertEqual(len(entry["bookmarks"]), 1)

    def test_remove_deletes_file_and_entry(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        store.remove("k")
        self.assertIsNone(store.find("k"))
        self.assertFalse((self.tmp / "books" / "k.json").exists())
        self.assertEqual(LibraryStore(self.tmp).entries, [])

    def test_save_is_atomic(self):
        store = LibraryStore(self.tmp)
        store.add_book("k", make_book())
        store.save()
        self.assertEqual(list(self.tmp.rglob("*.tmp")), [])
        data = json.loads((self.tmp / "library.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["books"][0]["key"], "k")

    def test_unwritable_dir_raises_library_error(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x", encoding="utf-8")
        with self.assertRaises(LibraryError):
            LibraryStore(blocker / "sub")


class HandEditedLibraryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        LibraryStore(self.tmp).add_book("k", make_book())

    def reload(self, mutate):
        path = self.tmp / "library.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return LibraryStore(self.tmp)

    def test_missing_optional_fields_get_defaults(self):
        def drop(data):
            for field in ("position", "bookmarks", "author", "source_url", "word_count"):
                del data["books"][0][field]

        entry = self.reload(drop).find("k")
        self.assertEqual(entry["position"], {"block": 0, "word": 0})
        self.assertEqual(entry["bookmarks"], [])
        self.assertEqual(entry["author"], "")
        self.assertEqual(entry["source_url"], "")
        self.assertEqual(entry["word_count"], 0)

    def test_missing_key_quarantines_file(self):
        store = self.reload(lambda d: d["books"][0].pop("key"))
        self.assertEqual(store.entries, [])
        self.assertIsNone(store.find("k"))
        self.assertTrue((self.tmp / "library.json.broken").exists())

    def test_title_not_string_quarantines_file(self):
        store = self.reload(lambda d: d["books"][0].__setitem__("title", 5))
        self.assertEqual(store.entries, [])
        self.assertTrue((self.tmp / "library.json.broken").exists())

    def test_position_as_list_becomes_start(self):
        store = self.reload(lambda d: d["books"][0].__setitem__("position", [1, 1]))
        self.assertEqual(store.find("k")["position"], {"block": 0, "word": 0})

    def test_position_fields_coerced_to_non_negative_ints(self):
        store = self.reload(
            lambda d: d["books"][0].__setitem__("position", {"block": "1", "word": -3})
        )
        self.assertEqual(store.find("k")["position"], {"block": 1, "word": 0})

    def test_malformed_bookmarks_dropped(self):
        marks = [
            {"block": "1", "word": 1, "label": "ok", "created_at": "t"},
            "junk",
            {"block": [], "word": 0, "label": "bad"},
        ]
        store = self.reload(lambda d: d["books"][0].__setitem__("bookmarks", marks))
        self.assertEqual(
            store.find("k")["bookmarks"],
            [{"block": 1, "word": 1, "label": "ok", "created_at": "t"}],
        )

    def test_wpm_clamped(self):
        self.assertEqual(self.reload(lambda d: d.__setitem__("wpm", 0)).wpm, 100)
        self.assertEqual(self.reload(lambda d: d.__setitem__("wpm", -5)).wpm, 100)
        self.assertEqual(self.reload(lambda d: d.__setitem__("wpm", 5000)).wpm, 1000)
        self.assertEqual(self.reload(lambda d: d.__setitem__("wpm", "x")).wpm, 250)

    def test_wpm_setter_clamps(self):
        store = LibraryStore(self.tmp)
        store.wpm = 0
        self.assertEqual(store.wpm, 100)


if __name__ == "__main__":
    unittest.main()
