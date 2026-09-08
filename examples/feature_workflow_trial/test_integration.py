from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path


class IntegratedToolkitTest(unittest.IsolatedAsyncioTestCase):
    async def run_toolkit(self, name: str, text: str) -> dict[str, str | int]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("cli.py")),
            "--name",
            name,
            "--text",
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        self.assertEqual(process.returncode, 0, stderr.decode())
        return json.loads(stdout)

    async def test_both_features_preserve_their_distinct_decisions(self) -> None:
        self.assertEqual(
            await self.run_toolkit("  dReW  ", "Lockin AI, works!"),
            {"greeting": "Hello, dReW!", "word_count": 3},
        )

    async def test_blank_input_has_independent_defaults(self) -> None:
        self.assertEqual(
            await self.run_toolkit(" ", " \t\n"),
            {"greeting": "Hello, friend!", "word_count": 0},
        )

    async def test_unicode_and_line_breaks(self) -> None:
        self.assertEqual(
            await self.run_toolkit("Drew ✨", "one\ttwo\nthree-four"),
            {"greeting": "Hello, Drew ✨!", "word_count": 3},
        )


if __name__ == "__main__":
    unittest.main()
