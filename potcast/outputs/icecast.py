"""Icecast output backend command construction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from potcast.models import Episode, IcecastOutputConfig, OutputError, OutputStatus
from potcast.outputs.base import (
    ProcessHandle,
    ProcessLauncher,
    SubprocessLauncher,
    require_local_file,
)


class IcecastOutputBackend:
    """Subprocess-backed Icecast output using ffmpeg."""

    def __init__(
        self,
        config: IcecastOutputConfig,
        *,
        launcher: ProcessLauncher | None = None,
        ffmpeg_command: str = "ffmpeg",
        volume: int = 100,
    ) -> None:
        self.config = config
        self.launcher = launcher or SubprocessLauncher()
        self.ffmpeg_command = ffmpeg_command
        self._process: ProcessHandle | None = None
        self._current_episode: Episode | None = None
        self._status = OutputStatus(backend="icecast", volume=volume)

    def start(self) -> None:
        if self._current_episode is None:
            self._status = replace(self._status, state="idle", connected=False, error=None)
            return
        self.play_episode(self._current_episode)

    def pause(self) -> None:
        self._terminate_process()
        self._status = replace(self._status, state="paused", connected=False, error=None)

    def stop(self) -> None:
        self._terminate_process()
        self._current_episode = None
        self._status = replace(
            self._status,
            state="stopped",
            connected=False,
            current_episode_identity=None,
            error=None,
        )

    def play_episode(self, episode: Episode) -> None:
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

    def build_command(self, episode: Episode, *, volume: int | None = None) -> list[str]:
        return build_ffmpeg_icecast_command(
            self.config,
            require_local_file(episode),
            ffmpeg_command=self.ffmpeg_command,
            volume=self._status.volume if volume is None else volume,
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


def _icecast_destination(config: IcecastOutputConfig) -> str:
    password = quote(config.source_password or "", safe="")
    mount = config.mount if config.mount.startswith("/") else f"/{config.mount}"
    return f"icecast://source:{password}@{config.host}:{config.port}{mount}"
