"""Contract tests for the disposable trial word counter."""

from __future__ import annotations

import unittest

from counter import count_words


class CountWordsTests(unittest.TestCase):
    def test_single_token_returns_integer(self) -> None:
        result = count_words("hello")
        self.assertIs(type(result), int)
        self.assertEqual(result, 1)

    def test_mixed_and_repeated_whitespace(self) -> None:
        self.assertEqual(count_words("  alpha\t\nbeta\u00a0gamma\r\n "), 3)

    def test_punctuation_remains_within_tokens(self) -> None:
        self.assertEqual(count_words("hello,world can't state-of-the-art !!!"), 4)

    def test_empty_string(self) -> None:
        self.assertEqual(count_words(""), 0)

    def test_whitespace_only(self) -> None:
        self.assertEqual(count_words(" \t\r\n\u00a0 "), 0)


if __name__ == "__main__":
    unittest.main()
