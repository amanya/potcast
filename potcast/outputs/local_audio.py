"""Local audio output backend command construction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from potcast.models import Episode, LocalAudioOutputConfig, OutputError, OutputStatus
from potcast.outputs.base import (
    ProcessHandle,
    ProcessLauncher,
    SubprocessLauncher,
    require_local_file,
)


class LocalAudioOutputBackend:
    """Subprocess-backed local audio output using mpv by default."""

    def __init__(
        self,
        config: LocalAudioOutputConfig,
        *,
        launcher: ProcessLauncher | None = None,
        volume: int = 100,
    ) -> None:
        self.config = config
        self.launcher = launcher or SubprocessLauncher()
        self._process: ProcessHandle | None = None
        self._current_episode: Episode | None = None
        self._status = OutputStatus(backend="local_audio", volume=volume)

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
        return build_mpv_local_audio_command(
            self.config,
            require_local_file(episode),
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


def build_mpv_local_audio_command(
    config: LocalAudioOutputConfig,
    audio_file: Path,
    *,
    volume: int = 100,
) -> list[str]:
    """Build the mpv command for local host audio playback."""

    command = [
        config.player,
        "--no-video",
        "--no-terminal",
        f"--volume={volume}",
        str(audio_file),
    ]
    if config.device:
        command.insert(-1, f"--audio-device={config.device}")
    if config.mixer == "software":
        command.insert(-1, "--softvol=yes")
    return command
