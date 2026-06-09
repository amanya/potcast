"""Shared output backend interfaces and test fakes."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from potcast.models import Episode, OutputError, OutputStatus


class OutputBackend(Protocol):
    """Common control surface for all Potcast output backends."""

    def start(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def play_episode(self, episode: Episode) -> None: ...

    def set_volume(self, volume: int) -> None: ...

    def status(self) -> OutputStatus: ...

    def consume_finished_episode(self) -> bool: ...


class ProcessHandle(Protocol):
    """Small process API used by subprocess-backed outputs."""

    def poll(self) -> object | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> object: ...


class ProcessLauncher(Protocol):
    """Launch dependency for subprocess-backed outputs."""

    def launch(self, command: list[str]) -> ProcessHandle: ...


class SubprocessLauncher:
    """Default process launcher used by production composition."""

    def launch(self, command: list[str]) -> ProcessHandle:
        return subprocess.Popen(command)  # noqa: S603


class FakeOutputBackend:
    """Output backend fake for service tests."""

    def __init__(self, *, backend: str = "fake", volume: int = 100) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self._status = OutputStatus(backend=backend, volume=volume)
        self._finished = False

    def start(self) -> None:
        self.calls.append(("start", None))
        self._status = replace(self._status, state="playing", connected=True, error=None)

    def pause(self) -> None:
        self.calls.append(("pause", None))
        self._status = replace(self._status, state="paused", connected=True, error=None)

    def stop(self) -> None:
        self.calls.append(("stop", None))
        self._status = replace(
            self._status,
            state="stopped",
            connected=False,
            current_episode_identity=None,
            error=None,
        )

    def play_episode(self, episode: Episode) -> None:
        self.calls.append(("play_episode", episode.identity))
        self._finished = False
        self._status = replace(
            self._status,
            state="playing",
            connected=True,
            current_episode_identity=episode.identity,
            error=None,
        )

    def set_volume(self, volume: int) -> None:
        self.calls.append(("set_volume", volume))
        self._status = replace(self._status, volume=volume, error=None)

    def fail(self, code: str, message: str) -> None:
        self._status = replace(
            self._status,
            state="error",
            connected=False,
            error=OutputError(code=code, message=message),
        )

    def finish_current_episode(self) -> None:
        self._finished = self._status.current_episode_identity is not None

    def consume_finished_episode(self) -> bool:
        if not self._finished:
            return False
        self._finished = False
        self._status = replace(self._status, state="idle", connected=False, error=None)
        return True

    def status(self) -> OutputStatus:
        return self._status


def require_local_file(episode: Episode) -> Path:
    """Return the local media path for a playable episode."""

    if episode.local_file is None:
        raise ValueError("Episode must have a local_file before output playback")
    return episode.local_file
