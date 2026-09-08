"""Verify the approved greeting contract through its public function."""

from __future__ import annotations

import unittest

from greeting import greet


class GreetingTests(unittest.TestCase):
    def test_greets_supplied_name(self) -> None:
        self.assertEqual(greet("Drew"), "Hello, Drew!")

    def test_preserves_mixed_case(self) -> None:
        self.assertEqual(greet("dReW"), "Hello, dReW!")

    def test_strips_exterior_whitespace(self) -> None:
        self.assertEqual(greet(" \tDrew\n "), "Hello, Drew!")

    def test_preserves_interior_whitespace(self) -> None:
        self.assertEqual(greet("Mary  Jane\tWatson"), "Hello, Mary  Jane\tWatson!")

    def test_uses_friend_for_empty_name(self) -> None:
        self.assertEqual(greet(""), "Hello, friend!")

    def test_uses_friend_for_whitespace_only_name(self) -> None:
        self.assertEqual(greet(" \t\n "), "Hello, friend!")


if __name__ == "__main__":
    unittest.main()
