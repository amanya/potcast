from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from potcast.models import DownloadMetadata
from potcast.runtime import build_output_backend, build_runtime
from potcast.state import JsonStateStore


class FakeProcess:
    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class RecordingLauncher:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def launch(self, command: list[str]) -> FakeProcess:
        self.commands.append(command)
        return FakeProcess()


def test_build_runtime_creates_directories_initial_state_services_and_app(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    runtime = build_runtime(config_path)

    assert runtime.config.storage.data_dir.is_dir()
    assert runtime.config.storage.episodes_dir.is_dir()
    assert runtime.services.state_store.load_runtime_state().volume == 42
    assert runtime.services.feeds.status().next_refresh_at is not None
    assert runtime.services.playback_scheduler.status().next_run_at is not None

    response = runtime.app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_runtime_status_works_before_real_feed_refresh(tmp_path: Path) -> None:
    runtime = build_runtime(write_config(tmp_path))

    response = runtime.app.test_client().get("/status")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"]["volume"] == 42
    assert payload["feed_monitor"]["next_refresh_at"] is not None


def test_build_runtime_preserves_existing_runtime_state(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, station_volume=42)
    first = build_runtime(config_path)
    first.services.station.set_volume(17)

    second = build_runtime(config_path)

    assert second.services.state_store.load_runtime_state().volume == 17


def test_runtime_start_wires_scheduler_and_start_on_boot_without_real_processes(
    tmp_path: Path,
) -> None:
    tasks: list[Callable[[], None]] = []
    launcher = RecordingLauncher()
    config_path = write_config(tmp_path, start_on_boot=True)
    runtime = build_runtime(
        config_path,
        process_launcher=launcher,
        background_starter=tasks.append,
    )
    store = JsonStateStore(runtime.config.storage.data_dir)
    downloads = store.load_download_metadata()
    downloads["history-extra"] = download_metadata(runtime.config.storage.episodes_dir)
    store.save_download_metadata(downloads)

    runtime.start()
    try:
        status = runtime.app.test_client().get("/status").get_json()["status"]
        scheduler_running = runtime.services.scheduler.status().running
    finally:
        runtime.stop()

    assert launcher.commands
    assert launcher.commands[0][0] == "ffmpeg"
    assert status["station_state"] == "playing"
    assert scheduler_running is True


def test_build_output_backend_uses_configured_primary_backend(tmp_path: Path) -> None:
    runtime = build_runtime(write_config(tmp_path, primary="local_audio"))

    backend = build_output_backend(runtime.config)

    assert backend.status().backend == "local_audio"
    assert backend.status().volume == 42


def write_config(
    tmp_path: Path,
    *,
    primary: str = "icecast",
    start_on_boot: bool = False,
    station_volume: int = 42,
) -> Path:
    data_dir = tmp_path / "data"
    episodes_dir = data_dir / "episodes"
    if primary == "icecast":
        enabled_backend = "icecast"
        icecast_enabled = "true"
        local_audio_enabled = "false"
    else:
        enabled_backend = "local_audio"
        icecast_enabled = "false"
        local_audio_enabled = "true"

    config_path = tmp_path / "potcast.yaml"
    config_path.write_text(
        dedent(
            f"""
            server:
              host: "127.0.0.1"
              port: 8089
            storage:
              data_dir: "{data_dir}"
              episodes_dir: "{episodes_dir}"
            station:
              start_on_boot: {str(start_on_boot).lower()}
              shuffle_podcasts: false
              volume: {station_volume}
            outputs:
              primary: "{primary}"
              enabled:
                - "{enabled_backend}"
              icecast:
                enabled: {icecast_enabled}
                source_password: "secret"
              local_audio:
                enabled: {local_audio_enabled}
            feeds:
              refresh_interval_minutes: 30
              download_timeout_seconds: 1
              user_agent: "Potcast/Test"
            channels:
              - id: "sleep"
                name: "Sleep"
                podcasts:
                  - id: "history-extra"
                    name: "History Extra"
                    feed_url: "https://example.com/history-extra.xml"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def download_metadata(episodes_dir: Path) -> DownloadMetadata:
    media_file = episodes_dir / "history-extra" / "episode.mp3"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"audio")
    return DownloadMetadata(
        podcast_id="history-extra",
        episode_identity="episode-1",
        media_url="https://cdn.example.com/episode.mp3",
        media_type="audio/mpeg",
        local_file=media_file,
        downloaded_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        title="Episode 1",
    )
