"""Transcribe one saved voice recording job into a Markdown transcript.

Run by voice_recorder.py as a separate process (the ``_`` prefix keeps the cog
loader from importing it) with the system Python that has faster-whisper:

    /usr/bin/python3 _transcribe_recording.py <job_dir> <out_md>

``job_dir`` holds ``meta.json`` plus one raw 48 kHz s16le stereo PCM file per
speaker, all aligned at t=0. Each speaker is transcribed separately so every
line is attributed, then lines are merged in time order. Output is written
atomically; the job dir is left for the caller to delete.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def _to_wav16k(pcm_path: Path, wav_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "48000", "-ac", "2"]
        + ["-i", str(pcm_path), "-ac", "1", "-ar", "16000", str(wav_path)],
        check=True,
    )


def main(job_dir: Path, out_md: Path) -> None:
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    meta = json.loads((job_dir / "meta.json").read_text())
    started = datetime.fromisoformat(meta["started_at"])
    model = WhisperModel(meta.get("model", "small.en"), device="cpu", compute_type="int8")

    lines: list[tuple[float, str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for track in meta["tracks"]:
            wav = Path(tmp) / (track["file"] + ".wav")
            _to_wav16k(job_dir / track["file"], wav)
            segments, _ = model.transcribe(
                str(wav),
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            for seg in segments:
                text = seg.text.strip()
                if text:
                    lines.append((seg.start, track["speaker"], text))
    lines.sort(key=lambda line: line[0])

    speakers = ", ".join(sorted({t["speaker"] for t in meta["tracks"]}))
    out = [
        f"# Voice transcript — {started:%Y-%m-%d %H:%M}",
        "",
        f"- Recording: `{meta['recording']}`",
        f"- Channel: {meta['channel']}",
        f"- Speakers: {speakers}",
        f"- Model: {meta.get('model', 'small.en')}",
        "",
    ]
    if not lines:
        out.append("_No speech detected._")
    for start, speaker, text in lines:
        stamp = (started + timedelta(seconds=start)).strftime("%H:%M:%S")
        out.append(f"**{stamp} — {speaker}:** {text}")
        out.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    tmp_md = out_md.with_suffix(".md.tmp")
    tmp_md.write_text("\n".join(out))
    tmp_md.replace(out_md)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
