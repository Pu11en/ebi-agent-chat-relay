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
import io
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

WATCHED_CHANNEL_ID = 1547437950466400306
AUDIO_OUT_DIR = Path("/home/drewp/main-projects/audio-content")

# discord voice PCM: 48kHz, 16-bit signed LE, stereo
_SAMPLE_RATE = 48000
_CHANNELS = 2
_SAMPLE_WIDTH = 2


class _MixedPCMSink:
    """A voice-recv AudioSink that appends every packet's PCM to one buffer.

    voice_recv delivers per-user packets; we don't try to align them — for a
    single-channel "record everything" use case, concatenating packets in
    arrival order produces intelligible audio for content-mining purposes.

    ``write`` is called from the library's PacketRouter thread for real audio
    and, separately, from SilenceGeneratorSink's own background thread for
    synthetic silence frames — both can land concurrently, so writes to the
    shared buffer must be serialized with a real thread lock (an asyncio.Lock
    is a no-op guard here since neither caller runs inside an event loop).
    """

    def __init__(self) -> None:
        self.buffer = io.BytesIO()
        self._lock = threading.Lock()

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.User | None, data) -> None:  # type: ignore[no-untyped-def]
        pcm = getattr(data, "pcm", None)
        if pcm:
            with self._lock:
                self.buffer.write(pcm)

    def cleanup(self) -> None:
        pass


def _encode_mp3(pcm_bytes: bytes, out_path: Path) -> None:
    """Pipe raw PCM through ffmpeg to produce an MP3."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "s16le",
        "-ar",
        str(_SAMPLE_RATE),
        "-ac",
        str(_CHANNELS),
        "-i",
        "pipe:0",
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(out_path),
    ]
    proc = subprocess.run(cmd, input=pcm_bytes, capture_output=True, check=False)
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
    _opus_patched = True


class VoiceRecorderCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._voice_client: discord.VoiceClient | None = None
        self._sink: _MixedPCMSink | None = None
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()

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

        try:
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        except Exception:
            logger.exception("Failed to connect to voice channel %s", channel.id)
            return

        sink = _MixedPCMSink()
        try:
            # SilenceGeneratorSink fills gaps from packet loss/jitter with
            # silence frames before they reach our sink; without it, dropped
            # or delayed packets splice directly together and produce audible
            # clicks/scratching in the mixed recording.
            vc.listen(voice_recv.SilenceGeneratorSink(voice_recv.BasicSink(sink.write)))  # type: ignore[attr-defined]
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

        if sink is None or sink.buffer.tell() == 0:
            logger.info("No audio captured; nothing to save")
            return

        stamp = (started or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
        out_path = AUDIO_OUT_DIR / f"{stamp}.mp3"
        pcm = sink.buffer.getvalue()
        try:
            await asyncio.to_thread(_encode_mp3, pcm, out_path)
            logger.info("Voice recording saved: %s (%d bytes PCM)", out_path, len(pcm))
        except Exception:
            logger.exception("Failed to encode/save MP3")


async def setup(bot: commands.Bot, runner: object, components: object) -> None:
    """Entry point for the custom Cog loader."""
    await bot.add_cog(VoiceRecorderCog(bot))
