#!/usr/bin/env python3
"""Persistent JSON-lines worker for local faster-whisper transcription."""

from __future__ import annotations

import argparse
import json
import sys
import wave


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


#: Below this, a clip is assumed to be one or two words — usually a tag on its
#: own — and the voice-activity filter is skipped rather than allowed to discard
#: the whole thing.
SHORT_CLIP_SECONDS = 2.5

#: The NATO phonetic alphabet, used as spoken thread tags. Offered to the decoder
#: as context so a single one of them is recognised rather than guessed at.
TAG_VOCABULARY = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu"
)


def _wav_seconds(path: str) -> float:
    """Duration of a local WAV, or 0.0 when it cannot be read.

    Falling back to 0.0 means an unreadable header is treated as a short clip —
    the filter is skipped and the audio gets its chance, which is the harmless
    direction to be wrong in.
    """
    try:
        with wave.open(path) as handle:
            rate = handle.getframerate() or 1
            return handle.getnframes() / rate
    except Exception:
        return 0.0


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
            # Discord already hands us one speaker's audio only while they are
            # speaking, so the voice-activity filter is a second opinion on a
            # question already answered — and on a very short clip it answers
            # wrongly. Measured: saying just the wake word ("alpha") produced a
            # ~0.5s clip that the filter stripped to nothing, so the command was
            # never even transcribed, let alone acted on. Long clips keep the
            # filter, where trimming genuine silence is worth having.
            duration = _wav_seconds(path)
            segments, _info = model.transcribe(
                path,
                vad_filter=duration >= SHORT_CLIP_SECONDS,
                beam_size=5,
                condition_on_previous_text=False,
                # Bias decoding towards the words used to address a thread. They
                # are ordinary English but rare in conversation, and one of them
                # alone on a half-second clip is exactly the hardest case.
                initial_prompt=TAG_VOCABULARY,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
            emit({"id": request_id, "text": text})
        except Exception as error:
            emit({"id": str(locals().get("request_id", "")), "error": str(error)})


if __name__ == "__main__":
    main()
