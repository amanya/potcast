from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from potcast.models import Episode, IcecastOutputConfig, LocalAudioOutputConfig
from potcast.outputs.base import FakeOutputBackend
from potcast.outputs.icecast import IcecastOutputBackend, build_ffmpeg_icecast_command
from potcast.outputs.local_audio import LocalAudioOutputBackend, build_mpv_local_audio_command


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.waited = False
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


class RecordingLauncher:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []

    def launch(self, command: list[str]) -> FakeProcess:
        process = FakeProcess()
        self.commands.append(command)
        self.processes.append(process)
        return process


class FailingLauncher:
    def launch(self, command: list[str]) -> FakeProcess:
        raise OSError("ffmpeg missing")


def episode(path: Path = Path("/data/episodes/history-extra/audio.mp3")) -> Episode:
    return Episode(
        title="New episode",
        identity="episode-guid",
        guid="episode-guid",
        published_at=datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        media_url="https://example.com/audio.mp3",
        media_type="audio/mpeg",
        local_file=path,
    )


def test_fake_backend_records_control_calls() -> None:
    backend = FakeOutputBackend(volume=70)
    playable = episode()

    backend.start()
    backend.pause()
    backend.play_episode(playable)
    backend.set_volume(55)
    backend.stop()

    assert backend.calls == [
        ("start", None),
        ("pause", None),
        ("play_episode", "episode-guid"),
        ("set_volume", 55),
        ("stop", None),
    ]
    assert backend.status().state == "stopped"
    assert backend.status().volume == 55


def test_icecast_backend_builds_expected_ffmpeg_command() -> None:
    config = IcecastOutputConfig(
        host="icecast.local",
        port=8000,
        source_password="change me",
        mount="/potcast.mp3",
        name="Potcast",
        description="Personal podcast radio",
        genre="Podcast",
        bitrate_kbps=128,
        sample_rate_hz=44100,
    )

    command = build_ffmpeg_icecast_command(config, Path("/media/show.mp3"), volume=70)

    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-i",
        "/media/show.mp3",
        "-vn",
        "-filter:a",
        "volume=0.70",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-content_type",
        "audio/mpeg",
        "-ice_name",
        "Potcast",
        "-ice_description",
        "Personal podcast radio",
        "-ice_genre",
        "Podcast",
        "-f",
        "mp3",
        "icecast://source:change%20me@icecast.local:8000/potcast.mp3",
    ]


def test_icecast_backend_uses_injected_launcher_without_starting_ffmpeg() -> None:
    launcher = RecordingLauncher()
    backend = IcecastOutputBackend(IcecastOutputConfig(source_password="secret"), launcher=launcher)

    backend.play_episode(episode())

    assert len(launcher.commands) == 1
    assert launcher.commands[0][0] == "ffmpeg"
    assert backend.status().state == "playing"
    assert backend.status().connected is True
    assert backend.status().current_episode_identity == "episode-guid"


def test_icecast_backend_detects_completed_process_without_real_subprocess() -> None:
    launcher = RecordingLauncher()
    backend = IcecastOutputBackend(IcecastOutputConfig(source_password="secret"), launcher=launcher)
    backend.play_episode(episode())

    running = backend.consume_finished_episode()
    launcher.processes[0].return_code = 0
    finished = backend.consume_finished_episode()
    duplicate = backend.consume_finished_episode()

    assert running is False
    assert finished is True
    assert duplicate is False
    assert backend.status().state == "idle"
    assert backend.status().connected is False


def test_local_audio_backend_builds_expected_mpv_command() -> None:
    config = LocalAudioOutputConfig(player="mpv", device="alsa/default", mixer="software")

    command = build_mpv_local_audio_command(config, Path("/media/show.mp3"), volume=42)

    assert command == [
        "mpv",
        "--no-video",
        "--no-terminal",
        "--volume=42",
        "--audio-device=alsa/default",
        "--softvol=yes",
        "/media/show.mp3",
    ]


def test_local_audio_backend_uses_injected_launcher_without_starting_mpv() -> None:
    launcher = RecordingLauncher()
    backend = LocalAudioOutputBackend(LocalAudioOutputConfig(enabled=True), launcher=launcher)

    backend.play_episode(episode())

    assert len(launcher.commands) == 1
    assert launcher.commands[0][0] == "mpv"
    assert backend.status().state == "playing"
    assert backend.status().connected is True


def test_backend_errors_surface_as_structured_output_status() -> None:
    backend = IcecastOutputBackend(
        IcecastOutputConfig(source_password="secret"),
        launcher=FailingLauncher(),
    )

    backend.play_episode(episode())

    status = backend.status()
    assert status.state == "error"
    assert status.connected is False
    assert status.error is not None
    assert status.error.code == "backend_start_failed"
    assert status.error.message == "ffmpeg missing"
