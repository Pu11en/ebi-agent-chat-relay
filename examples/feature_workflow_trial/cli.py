"""Run the two isolated trial features together."""

from __future__ import annotations

import argparse
import json

from counter import count_words
from greeting import greet


def main() -> None:
    parser = argparse.ArgumentParser(description="Lockin AI workflow trial")
    parser.add_argument("--name", required=True)
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    print(json.dumps({"greeting": greet(args.name), "word_count": count_words(args.text)}))


if __name__ == "__main__":
    main()
