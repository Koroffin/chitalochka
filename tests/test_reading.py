import unittest

from speedreader.reading import (
    ReadingModel,
    Word,
    delay_for,
    ends_clause,
    ends_sentence,
    split_words,
)


def spans(text):
    return [text[s:e] for s, e in split_words(text)]


def make_book(texts=("Раз два. Три четыре!", "Пять шесть.")):
    blocks = [
        {"type": "paragraph", "page": 1, "text": t} for t in texts[:-1]
    ] + [{"type": "heading", "page": 1, "text": texts[-1]}]
    return {"blocks": blocks}


class SplitAndTimingTests(unittest.TestCase):
    def test_split_words_keeps_punctuation_attached(self):
        self.assertEqual(spans("Привет, мир."), ["Привет,", "мир."])

    def test_lonely_dash_glued_to_previous_word(self):
        self.assertEqual(
            spans("— Привет, — сказал он."), ["— Привет, —", "сказал", "он."]
        )

    def test_only_punctuation_block_has_no_words(self):
        self.assertEqual(spans("* * *"), [])

    def test_preposition_glued_to_next_word(self):
        self.assertEqual(spans("Он шёл в дом, без шапки."), ["Он", "шёл", "в дом,", "без шапки."])
        self.assertEqual(spans("(В доме) тихо, У окна."), ["(В доме)", "тихо,", "У окна."])

    def test_preposition_at_block_end_stays_alone(self):
        self.assertEqual(spans("Он вошёл в"), ["Он", "вошёл", "в"])

    def test_preposition_with_trailing_punctuation_not_glued(self):
        self.assertEqual(spans("К. Иванов и с, запятой"), ["К.", "Иванов", "и", "с,", "запятой"])

    def test_preposition_chain_forms_one_group(self):
        self.assertEqual(spans("Кот из под стола"), ["Кот", "из под стола"])

    def test_model_counts_glued_group_as_one_word(self):
        m = ReadingModel({"blocks": [{"type": "paragraph", "text": "Я иду в парк с другом"}]})
        self.assertEqual(m.word_count, 4)
        self.assertEqual(m.words[2].text, "в парк")
        self.assertTrue(m.words[3].last_in_block)

    def test_ends_sentence_handles_closing_quotes(self):
        for t in ["конец.", "конец?»", "конец!)", "конец…", "т.е."]:
            self.assertTrue(ends_sentence(t), t)
        for t in ["нет,", "нет"]:
            self.assertFalse(ends_sentence(t), t)

    def test_ends_clause(self):
        for t in ["слово,", "слово;", "слово:", "слово—", "слово-", "слово–"]:
            self.assertTrue(ends_clause(t), t)
        self.assertFalse(ends_clause("слово"))

    def test_delay_uses_max_multiplier_not_product(self):
        w = Word(0, 0, 0, 5, "конец.", last_in_block=True)
        self.assertAlmostEqual(delay_for(w, 250), 60 / 250 * 2.5)

    def test_delay_clause(self):
        w = Word(0, 0, 0, 6, "слово,", last_in_block=False)
        self.assertAlmostEqual(delay_for(w, 300), 0.3)

    def test_delay_plain_and_sentence(self):
        plain = Word(0, 0, 0, 5, "слово", last_in_block=False)
        end = Word(0, 0, 0, 6, "слово.", last_in_block=False)
        self.assertAlmostEqual(delay_for(plain, 300), 0.2)
        self.assertAlmostEqual(delay_for(end, 300), 0.4)


class ReadingModelTests(unittest.TestCase):
    def setUp(self):
        self.m = ReadingModel(make_book())

    def test_word_count_and_positions(self):
        self.assertEqual(self.m.word_count, 6)
        self.assertEqual(self.m.words[2].position, (0, 2))
        self.assertTrue(self.m.words[3].last_in_block)
        w = self.m.words[1]
        self.assertEqual("Раз два. Три четыре!"[w.start:w.end], w.text)

    def test_advance_stops_at_end(self):
        for _ in range(5):
            self.assertTrue(self.m.advance())
        self.assertFalse(self.m.advance())
        self.assertEqual(self.m.pos, 5)

    def test_set_position_clamps(self):
        self.m.set_position(9, 9)
        self.assertEqual(self.m.position, (1, 1))
        self.m.set_position(0, 99)
        self.assertEqual(self.m.position, (0, 3))

    def test_prev_sentence_goes_to_start_of_current_then_previous(self):
        self.m.pos = 3
        self.m.prev_sentence()
        self.assertEqual(self.m.pos, 2)
        self.m.prev_sentence()
        self.assertEqual(self.m.pos, 0)

    def test_next_sentence(self):
        self.m.pos = 0
        self.m.next_sentence()
        self.assertEqual(self.m.pos, 2)
        self.m.pos = 4
        self.m.next_sentence()
        self.assertEqual(self.m.pos, 5)

    def test_block_navigation(self):
        self.m.pos = 0
        self.m.next_block()
        self.assertEqual(self.m.pos, 4)
        self.m.pos = 5
        self.m.prev_block()
        self.assertEqual(self.m.pos, 4)
        self.m.prev_block()
        self.assertEqual(self.m.pos, 0)

    def test_progress(self):
        self.m.pos = 3
        self.assertEqual(self.m.progress, 0.5)

    def test_delay_uses_current_word(self):
        self.m.pos = 1
        self.assertAlmostEqual(self.m.delay(300), 0.4)

    def test_empty_book(self):
        m = ReadingModel({"blocks": []})
        self.assertEqual(m.word_count, 0)
        self.assertIsNone(m.current)
        self.assertFalse(m.advance())
        self.assertEqual(m.progress, 0.0)
        m.prev_sentence()
        m.next_sentence()
        m.prev_block()
        m.next_block()
        m.set_position(3, 3)
        self.assertEqual(m.pos, 0)


if __name__ == "__main__":
    unittest.main()
