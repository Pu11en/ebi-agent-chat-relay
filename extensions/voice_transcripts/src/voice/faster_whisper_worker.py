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

#: Offering the tag vocabulary as an `initial_prompt` was tried and removed. It
#: biased the decoder so hard that near-silence came back *as* tag words — "yankee
#: zulu" from a half-second of room tone — and an invented tag can address a
#: thread. Helping it recognise a word is not worth teaching it to invent one.

#: Confidence floors for keeping a segment.
#:
#: These exist because a short clip with the voice filter off is exactly where
#: Whisper invents text, and it invents a small, recognisable canon: "Thanks for
#: watching!", "Thank you.", a syllable repeated six times. Both numbers come from
#: the decoder's own uncertainty rather than a blocklist of phrases, which would
#: only ever cover the hallucinations already seen.
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0


def _is_confident(segment: object) -> bool:
    """Whether the decoder actually believes what it just produced."""
    no_speech = getattr(segment, "no_speech_prob", 0.0) or 0.0
    avg_logprob = getattr(segment, "avg_logprob", 0.0) or 0.0
    return no_speech <= MAX_NO_SPEECH_PROB and avg_logprob >= MIN_AVG_LOGPROB


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
            )
            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip() and _is_confident(segment)
            ).strip()
            emit({"id": request_id, "text": text})
        except Exception as error:
            emit({"id": str(locals().get("request_id", "")), "error": str(error)})


if __name__ == "__main__":
    main()
