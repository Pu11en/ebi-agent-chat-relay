"""Voice channel recorder — joins a specific VC, records everyone, writes MP3.

Personal Cog: hardcoded to a single voice channel and a single output folder on
this machine.  Not part of the ccdb framework.

Flow:
    - When any human joins the watched VC and the bot is not connected, the bot
      joins and starts a mixed PCM recording via discord-ext-voice-recv.
    - When the last human leaves, the bot stops the sink, encodes the mixed PCM
      to MP3 via ffmpeg, saves it under ``AUDIO_OUT_DIR``, and disconnects.

Requires:
    pip install discord-ext-voice-recv
    ffmpeg on PATH
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

WATCHED_CHANNEL_ID = 1547437950466400306
AUDIO_OUT_DIR = Path("/home/drewp/main-projects/audio-content")
TRANSCRIPT_DIR = AUDIO_OUT_DIR / "transcripts"
# Per-speaker PCM waiting to be transcribed. On disk (not memory) so a bot
# restart mid-transcription resumes the job instead of losing the transcript.
PENDING_DIR = AUDIO_OUT_DIR / ".pending-transcripts"
WHISPER_PYTHON = "/usr/bin/python3"  # system Python with faster-whisper installed
WHISPER_MODEL = "small.en"
_TRANSCRIBER = Path(__file__).with_name("_transcribe_recording.py")

# discord voice PCM: 48kHz, 16-bit signed LE, stereo
_SAMPLE_RATE = 48000
_CHANNELS = 2
_SAMPLE_WIDTH = 2


_FRAME_BYTES = _CHANNELS * _SAMPLE_WIDTH  # bytes per stereo sample
# If a stream's RTP clock drifts this far from wall-clock placement (speaker
# rejoined, SSRC reused, timestamp reset), re-anchor it to wall clock.
_REANCHOR_SAMPLES = 2 * _SAMPLE_RATE


class _MixedPCMSink:
    """A voice-recv AudioSink that places each frame on a timeline by RTP timestamp.

    Previously frames were appended in arrival order, with SilenceGeneratorSink
    inserting 20ms of digital silence whenever a packet was merely *late*. The
    late packet was then appended after that silence, so every bit of network
    jitter cut a hard gap into the middle of speech (audible as clicks and
    screeches) and pushed everything after it out of place.

    Now each SSRC gets its own track. A stream's first frame is anchored at
    its wall-clock arrival time; every later frame is written at
    ``anchor + (rtp_ts - first_ts)``, so late frames land where they belong
    and real pauses become zeros. Tracks are summed into one mix at the end.

    ``write`` runs on the library's PacketRouter thread, so the lock is a real
    thread lock (an asyncio.Lock would guard nothing here).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._tracks: dict[int, bytearray] = {}
        self._anchors: dict[int, tuple[int, int]] = {}  # ssrc -> (offset, first_ts)
        self._speakers: dict[int, str] = {}  # ssrc -> display name

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.User | None, data) -> None:  # type: ignore[no-untyped-def]
        pcm = getattr(data, "pcm", None)
        packet = getattr(data, "packet", None)
        if not pcm or packet is None:
            return
        ssrc, ts = packet.ssrc, packet.timestamp
        wall = int((time.monotonic() - self._t0) * _SAMPLE_RATE)
        with self._lock:
            if user is not None and ssrc not in self._speakers:
                self._speakers[ssrc] = getattr(user, "display_name", None) or str(user)
            anchor = self._anchors.get(ssrc)
            if anchor is not None:
                offset = anchor[0] + ((ts - anchor[1]) % 2**32)
            if anchor is None or abs(offset - wall) > _REANCHOR_SAMPLES:
                self._anchors[ssrc] = (wall, ts)
                offset = wall
            track = self._tracks.setdefault(ssrc, bytearray())
            start = offset * _FRAME_BYTES
            end = start + len(pcm)
            if len(track) < end:
                track.extend(bytes(end - len(track)))
            track[start:end] = pcm

    @property
    def has_audio(self) -> bool:
        return any(self._tracks.values())

    def tracks(self) -> list[bytes]:
        """One int16 stereo PCM stream per speaker, all aligned at t=0."""
        return [pcm for _, pcm in self.named_tracks()]

    def named_tracks(self) -> list[tuple[str, bytes]]:
        with self._lock:
            return [
                (self._speakers.get(ssrc, f"speaker-{ssrc}"), bytes(t))
                for ssrc, t in self._tracks.items()
                if t
            ]

    def cleanup(self) -> None:
        pass


def _encode_mp3(tracks: list[bytes], out_path: Path) -> None:
    """Mix per-speaker raw PCM tracks and encode to MP3 with ffmpeg."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["ffmpeg", "-y"]
        for i, pcm in enumerate(tracks):
            raw = Path(tmp) / f"track{i}.pcm"
            raw.write_bytes(pcm)
            cmd += ["-f", "s16le", "-ar", str(_SAMPLE_RATE), "-ac", str(_CHANNELS), "-i", str(raw)]
        if len(tracks) > 1:
            # normalize=0: plain sum, so one speaker isn't quieter because another joined
            cmd += ["-filter_complex", f"amix=inputs={len(tracks)}:duration=longest:normalize=0"]
        cmd += ["-codec:a", "libmp3lame", "-qscale:a", "2", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        logger.error("ffmpeg failed (%d): %s", proc.returncode, stderr)


_opus_patched = False


def _patch_opus_decoder_error_swallowing() -> None:
    """Conceal corrupted opus packets instead of dropping them, and keep a
    last-resort guard so a truly unrecoverable decode error can't kill the
    receive thread.

    ``Decoder.decode`` raises ``OpusError`` when a packet's opus payload is
    corrupt. Previously we caught that at the outer ``pop_data`` layer and
    simply skipped the frame — but skipping a frame mid-speech splices the
    waveform on either side of the gap directly together, producing an
    audible click. Every corrupted packet was an audible click, and on a
    lossy connection that's most of what you'd hear. Opus has a built-in
    packet-loss-concealment mode (``decode(None)``) that synthesizes a
    smooth continuation from the decoder's internal state instead of a hard
    edge — call it as a fallback so corrupted frames are inaudible rather
    than clicks. ``pop_data`` is still wrapped as a last-resort guard for any
    other exception, since discord-ext-voice-recv's PacketRouter treats any
    exception there as fatal to the receive thread.
    """
    global _opus_patched
    if _opus_patched:
        return
    from discord.ext.voice_recv import opus as _vr_opus  # type: ignore[import-not-found]
    from discord.opus import Decoder as _OpusDecoder  # type: ignore[import-not-found]
    from discord.opus import OpusError

    original_decode = _OpusDecoder.decode

    def decode_with_concealment(self, data, *, fec=False):  # type: ignore[no-untyped-def]
        try:
            return original_decode(self, data, fec=fec)
        except OpusError as exc:
            if data is None:
                raise
            logger.debug("Concealing corrupted opus packet: %s", exc)
            return original_decode(self, None, fec=False)

    _OpusDecoder.decode = decode_with_concealment  # type: ignore[method-assign]

    original_pop_data = _vr_opus.PacketDecoder.pop_data

    def safe_pop_data(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return original_pop_data(self, *args, **kwargs)
        except OpusError as exc:
            logger.debug("Dropping corrupted opus packet: %s", exc)
            return None

    _vr_opus.PacketDecoder.pop_data = safe_pop_data  # type: ignore[method-assign]

    # Discord voice is end-to-end encrypted (DAVE). discord.py negotiates DAVE
    # and encrypts what it *sends*, but discord-ext-voice-recv never
    # DAVE-decrypts what it *receives* — so the opus decoder was fed
    # ciphertext, which decodes into scratchy noise during every speech burst.
    # Decrypt each frame as it enters the jitter buffer (so FEC lookahead sees
    # plaintext too). DAVE frames end with the 0xFAFA magic marker; frames
    # without it (passthrough / non-E2EE calls) are left untouched.
    original_push_packet = _vr_opus.PacketDecoder.push_packet

    def push_packet_with_dave(self, packet):  # type: ignore[no-untyped-def]
        data = getattr(packet, "decrypted_data", None)
        if data and data[-2:] == _DAVE_MAGIC:
            packet.decrypted_data = _dave_decrypt(self, data)
            key = "decrypted" if packet.decrypted_data else "undecryptable"
        else:
            key = "plain" if data else "empty"
        _dave_stats[key] = _dave_stats.get(key, 0) + 1
        return original_push_packet(self, packet)

    _vr_opus.PacketDecoder.push_packet = push_packet_with_dave  # type: ignore[method-assign]

    # Each time a speaker starts talking after a pause (Discord stops sending
    # during silence), the library decoded the new burst's first frame with
    # (a) the decoder state left over from the previous burst, and (b) FEC
    # recovery from a packet that belongs to the other side of the pause. Both
    # produce a loud one-frame screech; measured with real opus, a stale
    # decoder peaks at 0.23-0.91 full scale where the true audio is 0.00-0.29.
    # Start every talk burst on a fresh decoder, and only use FEC when the
    # next packet really is the frame right after the missing one.
    original_decode_packet = _vr_opus.PacketDecoder._decode_packet
    frame = _OpusDecoder.SAMPLES_PER_FRAME

    def decode_packet_per_burst(self, packet):  # type: ignore[no-untyped-def]
        if packet:
            last_ts = self._last_ts
            if last_ts >= 0 and (packet.timestamp - last_ts) % 2**32 != frame:
                self._decoder = _OpusDecoder()
            return original_decode_packet(self, packet)
        next_packet = self._buffer.peek_next()
        if next_packet is not None and (next_packet.timestamp - packet.timestamp) % 2**32 != frame:
            return packet, self._decoder.decode(None, fec=False)
        return original_decode_packet(self, packet)

    _vr_opus.PacketDecoder._decode_packet = decode_packet_per_burst  # type: ignore[method-assign]
    _opus_patched = True


_DAVE_MAGIC = b"\xfa\xfa"
# Per-recording counters, logged on stop — tells us whether leftover noise is
# frames we couldn't decrypt or something downstream.
_dave_stats: dict[str, int] = {}


def _dave_decrypt(decoder, data: bytes) -> bytes:  # type: ignore[no-untyped-def]
    """DAVE-decrypt one opus frame for the decoder's SSRC.

    Returns ``b""`` when the frame can't be decrypted (unknown speaker, session
    not ready, bad key) — an empty frame fails opus decode and falls through to
    packet-loss concealment, which is far quieter than decoding ciphertext.
    """
    import davey  # type: ignore[import-not-found]

    vc = decoder.sink.voice_client
    conn = getattr(vc, "_connection", None)
    session = getattr(conn, "dave_session", None)
    if session is None or not session.ready:
        return b""
    user_id = decoder._cached_id or vc._get_id_from_ssrc(decoder.ssrc)
    if not user_id:
        return b""
    decoder._cached_id = user_id
    try:
        return session.decrypt(user_id, davey.MediaType.audio, data)
    except Exception as exc:
        logger.debug("DAVE decrypt failed for ssrc=%s: %s", decoder.ssrc, exc)
        return b""


class VoiceRecorderCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._voice_client: discord.VoiceClient | None = None
        self._sink: _MixedPCMSink | None = None
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()
        # One transcription at a time: whisper saturates the CPU.
        self._transcribe_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def cog_load(self) -> None:
        # Resume any job a previous run didn't finish (bot restarted mid-way).
        for job in sorted(PENDING_DIR.glob("*/meta.json")) if PENDING_DIR.exists() else []:
            self._spawn_transcription(job.parent)

    def _spawn_transcription(self, job_dir: Path) -> None:
        task = asyncio.create_task(self._transcribe(job_dir))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _transcribe(self, job_dir: Path) -> None:
        async with self._transcribe_lock:
            out_md = TRANSCRIPT_DIR / f"{job_dir.name}.md"
            proc = await asyncio.create_subprocess_exec(
                WHISPER_PYTHON,
                str(_TRANSCRIBER),
                str(job_dir),
                str(out_md),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "Transcription failed for %s (%s): %s",
                    job_dir.name,
                    proc.returncode,
                    stderr.decode(errors="replace")[-2000:],
                )
                return  # job dir kept; retried on next cog load
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.info("Transcript saved: %s", out_md)
        await self._post_transcript(out_md)

    async def _post_transcript(self, out_md: Path) -> None:
        """Drop the transcript into the voice channel's text chat."""
        channel = self.bot.get_channel(WATCHED_CHANNEL_ID)
        if not isinstance(channel, discord.VoiceChannel):
            return
        text = out_md.read_text()
        body = "\n".join(ln for ln in text.splitlines() if ln.startswith("**")) or text
        preview = body if len(body) <= 1800 else body[:1800] + "\n…"
        with contextlib.suppress(discord.HTTPException):
            await channel.send(
                f"📝 Transcript ready\n{preview}",
                file=discord.File(out_md),
            )

    @staticmethod
    def _human_members(channel: discord.VoiceChannel) -> list[discord.Member]:
        return [m for m in channel.members if not m.bot]

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None
        logger.info(
            "voice_state_update: user=%s before=%s after=%s watched=%s",
            member.id,
            before_id,
            after_id,
            WATCHED_CHANNEL_ID,
        )
        if WATCHED_CHANNEL_ID not in (before_id, after_id):
            return

        async with self._lock:
            channel = self.bot.get_channel(WATCHED_CHANNEL_ID)
            logger.info("resolved channel=%r vc_connected=%s", channel, self._voice_client)
            if not isinstance(channel, discord.VoiceChannel):
                logger.warning("watched channel %s is not a VoiceChannel", WATCHED_CHANNEL_ID)
                return

            humans = self._human_members(channel)
            logger.info("humans in channel: %s", [m.id for m in humans])
            if humans and self._voice_client is None:
                await self._start(channel)
            elif not humans and self._voice_client is not None:
                await self._stop()

    async def _start(self, channel: discord.VoiceChannel) -> None:
        try:
            from discord.ext import voice_recv  # type: ignore[import-not-found]
        except ImportError:
            logger.error(
                "discord-ext-voice-recv not installed — cannot record. "
                "Run: uv pip install discord-ext-voice-recv"
            )
            return

        _patch_opus_decoder_error_swallowing()
        _dave_stats.clear()

        try:
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        except Exception:
            logger.exception("Failed to connect to voice channel %s", channel.id)
            return

        sink = _MixedPCMSink()
        try:
            # No SilenceGeneratorSink: the sink places frames by RTP timestamp,
            # so gaps are already silence and late packets aren't cut apart.
            vc.listen(voice_recv.BasicSink(sink.write))  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Failed to start listening; disconnecting")
            await vc.disconnect(force=True)
            return

        self._voice_client = vc
        self._sink = sink
        self._started_at = datetime.now()
        logger.info("Voice recording started in channel %s", channel.id)

    async def _stop(self) -> None:
        vc = self._voice_client
        sink = self._sink
        started = self._started_at
        self._voice_client = None
        self._sink = None
        self._started_at = None

        if vc is None:
            return

        try:
            try:
                vc.stop_listening()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("stop_listening raised", exc_info=True)
            await vc.disconnect(force=True)
        except Exception:
            logger.exception("Disconnect from voice failed")

        if sink is None or not sink.has_audio:
            logger.info("No audio captured; nothing to save")
            return

        stamp = (started or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
        out_path = AUDIO_OUT_DIR / f"{stamp}.mp3"
        named = sink.named_tracks()
        tracks = [pcm for _, pcm in named]
        logger.info("DAVE frame stats: %s", _dave_stats)
        try:
            await asyncio.to_thread(_encode_mp3, tracks, out_path)
            logger.info(
                "Voice recording saved: %s (%d tracks, %d bytes PCM)",
                out_path,
                len(tracks),
                sum(len(t) for t in tracks),
            )
        except Exception:
            logger.exception("Failed to encode/save MP3")

        try:
            job_dir = await asyncio.to_thread(
                _write_transcript_job,
                stamp,
                started or datetime.now(),
                out_path.name,
                named,
                getattr(self.bot.get_channel(WATCHED_CHANNEL_ID), "name", str(WATCHED_CHANNEL_ID)),
            )
        except Exception:
            logger.exception("Failed to queue transcription")
            return
        self._spawn_transcription(job_dir)


def _write_transcript_job(
    stamp: str,
    started: datetime,
    recording: str,
    named: list[tuple[str, bytes]],
    channel_name: str = "",
) -> Path:
    job_dir = PENDING_DIR / stamp
    job_dir.mkdir(parents=True, exist_ok=True)
    tracks = []
    for i, (speaker, pcm) in enumerate(named):
        (job_dir / f"track{i}.pcm").write_bytes(pcm)
        tracks.append({"file": f"track{i}.pcm", "speaker": speaker})
    meta = {
        "started_at": started.isoformat(),
        "recording": recording,
        "channel": channel_name,
        "model": WHISPER_MODEL,
        "tracks": tracks,
    }
    # meta.json last: its presence marks the job complete and resumable.
    (job_dir / "meta.json").write_text(json.dumps(meta))
    return job_dir


async def setup(bot: commands.Bot, runner: object, components: object) -> None:
    """Entry point for the custom Cog loader."""
    await bot.add_cog(VoiceRecorderCog(bot))
