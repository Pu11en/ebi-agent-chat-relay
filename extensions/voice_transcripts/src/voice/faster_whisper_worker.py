#!/usr/bin/env python3
"""Persistent JSON-lines worker for local faster-whisper transcription."""

from __future__ import annotations

import argparse
import json
import sys


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="base.en")
    parser.add_argument("--contract", action="store_true")
    args = parser.parse_args()

    if args.contract:
        emit(
            {
                "contract": 1,
                "request": {"id": "string", "path": "string"},
                "response": {"id": "string", "text": "string"},
            }
        )
        return

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(args.model, device="cpu", compute_type="int8")
    except Exception as error:
        emit({"ready": False, "error": str(error)})
        return

    emit({"ready": True, "model": args.model})
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            request_id = str(request["id"])
            path = str(request["path"])
            segments, _info = model.transcribe(
                path,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
            emit({"id": request_id, "text": text})
        except Exception as error:
            emit({"id": str(locals().get("request_id", "")), "error": str(error)})


if __name__ == "__main__":
    main()
