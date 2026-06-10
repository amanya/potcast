"""Icecast output backend command construction."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from potcast.models import (
    Episode,
    IcecastOutputConfig,
    OutputError,
    OutputPlaybackEvent,
    OutputStatus,
)
from potcast.outputs.base import (
    ProcessHandle,
    ProcessLauncher,
    SubprocessLauncher,
    require_local_file,
)


class IcecastOutputBackend:
    """Subprocess-backed Icecast output using ffmpeg."""

    _CHANNELS = 2
    _SAMPLE_WIDTH_BYTES = 2
    _SILENCE_SECONDS = 0.1

    def __init__(
        self,
        config: IcecastOutputConfig,
        *,
        launcher: ProcessLauncher | None = None,
        ffmpeg_command: str = "ffmpeg",
        volume: int = 100,
    ) -> None:
        self.config = config
        self._uses_injected_launcher = launcher is not None
        self.launcher = launcher or SubprocessLauncher()
        self.ffmpeg_command = ffmpeg_command
        self._process: ProcessHandle | None = None
        self._stream_process: subprocess.Popen[bytes] | None = None
        self._decoder_process: subprocess.Popen[bytes] | None = None
        self._relay_thread: threading.Thread | None = None
        self._relay_stop = threading.Event()
        self._lock = threading.RLock()
        self._playback_event: OutputPlaybackEvent | None = None
        self._current_episode: Episode | None = None
        self._status = OutputStatus(backend="icecast", volume=volume)

    def start(self) -> None:
        if self._current_episode is None:
            self._status = replace(self._status, state="idle", connected=False, error=None)
            return
        self.play_episode(self._current_episode)

    def pause(self) -> None:
        if self._uses_injected_launcher:
            self._terminate_process()
            self._status = replace(self._status, state="paused", connected=False, error=None)
            return
        self._terminate_decoder()
        self._status = replace(
            self._status,
            state="paused",
            connected=self._stream_process is not None,
            error=None,
        )

    def stop(self) -> None:
        self._terminate_processes()
        self._current_episode = None
        self._status = replace(
            self._status,
            state="stopped",
            connected=False,
            current_episode_identity=None,
            error=None,
        )

    def play_episode(self, episode: Episode) -> None:
        if not self._uses_injected_launcher:
            self._play_episode_continuous(episode)
            return

        self._current_episode = episode
        try:
            command = self.build_command(episode, volume=self._status.volume)
            self._terminate_process()
            self._process = self.launcher.launch(command)
        except (OSError, ValueError) as exc:
            self._process = None
            self._status = replace(
                self._status,
                state="error",
                connected=False,
                current_episode_identity=episode.identity,
                error=OutputError(code="backend_start_failed", message=str(exc)),
            )
            return

        self._status = replace(
            self._status,
            state="playing",
            connected=True,
            current_episode_identity=episode.identity,
            error=None,
        )

    def set_volume(self, volume: int) -> None:
        self._status = replace(self._status, volume=volume, error=None)
        if self._current_episode is not None and self._status.state == "playing":
            self.play_episode(self._current_episode)

    def status(self) -> OutputStatus:
        return self._status

    def consume_playback_event(self) -> OutputPlaybackEvent | None:
        if not self._uses_injected_launcher:
            return self._consume_continuous_playback_event()

        if self._process is None:
            return None
        return_code = self._process.poll()
        if return_code is None:
            return None
        self._process = None
        if return_code == 0:
            self._status = replace(self._status, state="idle", connected=False, error=None)
            return OutputPlaybackEvent(outcome="completed")

        error = OutputError(
            code="backend_process_failed",
            message=f"Output process exited unexpectedly with code {return_code}.",
        )
        self._status = replace(self._status, state="error", connected=False, error=error)
        return OutputPlaybackEvent(outcome="failed", error=error)

    def consume_finished_episode(self) -> bool:
        event = self.consume_playback_event()
        return event is not None and event.outcome == "completed"

    def build_command(self, episode: Episode, *, volume: int | None = None) -> list[str]:
        return build_ffmpeg_icecast_command(
            self.config,
            require_local_file(episode),
            ffmpeg_command=self.ffmpeg_command,
            volume=self._status.volume if volume is None else volume,
        )

    def _play_episode_continuous(self, episode: Episode) -> None:
        self._current_episode = episode
        try:
            audio_file = require_local_file(episode)
            with self._lock:
                self._ensure_stream_process()
                old_decoder = self._pop_decoder_locked()
                self._playback_event = None

            self._terminate_decoder_process(old_decoder)
            decoder_process = subprocess.Popen(  # noqa: S603
                build_ffmpeg_decode_to_pcm_command(
                    audio_file,
                    ffmpeg_command=self.ffmpeg_command,
                    volume=self._status.volume,
                    sample_rate_hz=self.config.sample_rate_hz,
                    channels=self._CHANNELS,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            with self._lock:
                self._decoder_process = decoder_process
                self._playback_event = None
                if self._status.state != "error":
                    self._status = replace(
                        self._status,
                        state="playing",
                        connected=True,
                        current_episode_identity=episode.identity,
                        error=None,
                    )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            with self._lock:
                self._decoder_process = None
                self._status = replace(
                    self._status,
                    state="error",
                    connected=self._stream_process is not None,
                    current_episode_identity=episode.identity,
                    error=OutputError(code="backend_start_failed", message=str(exc)),
                )
            return

    def _ensure_stream_process(self) -> None:
        if self._stream_process is not None and self._stream_process.poll() is None:
            return

        command = build_ffmpeg_icecast_stream_command(
            self.config,
            ffmpeg_command=self.ffmpeg_command,
            channels=self._CHANNELS,
        )
        self._stream_process = subprocess.Popen(command, stdin=subprocess.PIPE)  # noqa: S603
        self._relay_stop.clear()
        if self._relay_thread is None or not self._relay_thread.is_alive():
            self._relay_thread = threading.Thread(target=self._relay_audio, daemon=True)
            self._relay_thread.start()

    def _relay_audio(self) -> None:
        silence = self._silence_chunk()
        while not self._relay_stop.is_set():
            with self._lock:
                stream_process = self._stream_process
                decoder_process = self._decoder_process
            if stream_process is None or stream_process.stdin is None:
                time.sleep(self._SILENCE_SECONDS)
                continue

            decoder_stdout = decoder_process.stdout if decoder_process is not None else None
            if decoder_process is not None and decoder_stdout is not None:
                chunk = decoder_stdout.read(8192)
                if chunk:
                    self._write_stream_chunk(stream_process, chunk)
                    continue

                return_code = decoder_process.poll()
                if return_code is None:
                    continue
                with self._lock:
                    if self._decoder_process is decoder_process:
                        self._decoder_process = None
                        if return_code == 0:
                            self._playback_event = OutputPlaybackEvent(outcome="completed")
                        else:
                            error = OutputError(
                                code="backend_process_failed",
                                message=(
                                    "Episode decoder exited unexpectedly "
                                    f"with code {return_code}."
                                ),
                            )
                            self._playback_event = OutputPlaybackEvent(
                                outcome="failed",
                                error=error,
                            )
                            self._status = replace(
                                self._status,
                                state="error",
                                connected=True,
                                error=error,
                            )
                continue

            self._write_stream_chunk(stream_process, silence)
            time.sleep(self._SILENCE_SECONDS)

    def _write_stream_chunk(
        self,
        stream_process: subprocess.Popen[bytes],
        chunk: bytes,
    ) -> None:
        try:
            if stream_process.stdin is not None:
                stream_process.stdin.write(chunk)
                stream_process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            error = OutputError(code="backend_process_failed", message=str(exc))
            with self._lock:
                self._playback_event = OutputPlaybackEvent(outcome="failed", error=error)
                self._status = replace(
                    self._status,
                    state="error",
                    connected=False,
                    error=error,
                )
                self._stream_process = None

    def _silence_chunk(self) -> bytes:
        byte_count = int(
            self.config.sample_rate_hz
            * self._CHANNELS
            * self._SAMPLE_WIDTH_BYTES
            * self._SILENCE_SECONDS
        )
        frame_size = self._CHANNELS * self._SAMPLE_WIDTH_BYTES
        byte_count -= byte_count % frame_size
        return b"\x00" * byte_count

    def _consume_continuous_playback_event(self) -> OutputPlaybackEvent | None:
        with self._lock:
            stream_process = self._stream_process
            event = self._playback_event
            self._playback_event = None

        if stream_process is not None:
            return_code = stream_process.poll()
            if return_code is not None:
                error = OutputError(
                    code="backend_process_failed",
                    message=f"Icecast stream process exited unexpectedly with code {return_code}.",
                )
                self._status = replace(
                    self._status,
                    state="error",
                    connected=False,
                    error=error,
                )
                return OutputPlaybackEvent(outcome="failed", error=error)

        if event is not None and event.outcome == "completed":
            self._status = replace(self._status, state="idle", connected=True, error=None)
        return event

    def _terminate_processes(self) -> None:
        if self._uses_injected_launcher:
            self._terminate_process()
            return
        self._relay_stop.set()
        self._terminate_decoder()
        self._terminate_stream_process()

    def _terminate_decoder(self) -> None:
        with self._lock:
            process = self._pop_decoder_locked()
        self._terminate_decoder_process(process)

    def _pop_decoder_locked(self) -> subprocess.Popen[bytes] | None:
        process = self._decoder_process
        self._decoder_process = None
        return process

    def _terminate_decoder_process(self, process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._status = replace(
                self._status,
                state="error",
                error=OutputError(code="backend_stop_failed", message=str(exc)),
            )

    def _terminate_stream_process(self) -> None:
        with self._lock:
            process = self._stream_process
            self._stream_process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._status = replace(
                self._status,
                state="error",
                connected=False,
                error=OutputError(code="backend_stop_failed", message=str(exc)),
            )

    def _terminate_process(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except OSError as exc:
            self._status = replace(
                self._status,
                state="error",
                connected=False,
                error=OutputError(code="backend_stop_failed", message=str(exc)),
            )
        finally:
            self._process = None


def build_ffmpeg_icecast_command(
    config: IcecastOutputConfig,
    audio_file: Path,
    *,
    ffmpeg_command: str = "ffmpeg",
    volume: int = 100,
) -> list[str]:
    """Build the ffmpeg command for streaming one local episode to Icecast."""

    codec = "libmp3lame" if config.format == "mp3" else "aac"
    output_format = "mp3" if config.format == "mp3" else config.format
    content_type = "audio/mpeg" if config.format == "mp3" else f"audio/{config.format}"
    destination = _icecast_destination(config)

    return [
        ffmpeg_command,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-i",
        str(audio_file),
        "-vn",
        "-filter:a",
        f"volume={volume / 100:.2f}",
        "-acodec",
        codec,
        "-b:a",
        f"{config.bitrate_kbps}k",
        "-ar",
        str(config.sample_rate_hz),
        "-content_type",
        content_type,
        "-ice_name",
        config.name,
        "-ice_description",
        config.description,
        "-ice_genre",
        config.genre,
        "-f",
        output_format,
        destination,
    ]


def build_ffmpeg_icecast_stream_command(
    config: IcecastOutputConfig,
    *,
    ffmpeg_command: str = "ffmpeg",
    channels: int = 2,
) -> list[str]:
    """Build the persistent ffmpeg command that owns the Icecast source."""

    codec = "libmp3lame" if config.format == "mp3" else "aac"
    output_format = "mp3" if config.format == "mp3" else config.format
    content_type = "audio/mpeg" if config.format == "mp3" else f"audio/{config.format}"
    destination = _icecast_destination(config)

    return [
        ffmpeg_command,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "s16le",
        "-ar",
        str(config.sample_rate_hz),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        "-vn",
        "-acodec",
        codec,
        "-b:a",
        f"{config.bitrate_kbps}k",
        "-ar",
        str(config.sample_rate_hz),
        "-content_type",
        content_type,
        "-ice_name",
        config.name,
        "-ice_description",
        config.description,
        "-ice_genre",
        config.genre,
        "-f",
        output_format,
        destination,
    ]


def build_ffmpeg_decode_to_pcm_command(
    audio_file: Path,
    *,
    ffmpeg_command: str = "ffmpeg",
    volume: int = 100,
    sample_rate_hz: int = 44100,
    channels: int = 2,
) -> list[str]:
    """Build the ffmpeg command that decodes one episode into raw PCM."""

    return [
        ffmpeg_command,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-i",
        str(audio_file),
        "-vn",
        "-filter:a",
        f"volume={volume / 100:.2f}",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate_hz),
        "-ac",
        str(channels),
        "pipe:1",
    ]


def _icecast_destination(config: IcecastOutputConfig) -> str:
    password = quote(config.source_password or "", safe="")
    mount = config.mount if config.mount.startswith("/") else f"/{config.mount}"
    return f"icecast://source:{password}@{config.host}:{config.port}{mount}"
