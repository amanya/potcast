from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from potcast.models import (
    DownloadMetadata,
    Episode,
    FeedMetadata,
    OutputError,
    RuntimeState,
    StorageConfig,
)
from potcast.state import JsonStateStore, ensure_data_directories


def episode() -> Episode:
    return Episode(
        title="New episode",
        identity="episode-guid",
        guid="episode-guid",
        published_at=datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        media_url="https://example.com/audio.mp3",
        media_type="audio/mpeg",
        duration="12:34",
        local_file=Path("/data/episodes/show/show.mp3"),
        downloaded_at=datetime(2024, 1, 2, 11, tzinfo=timezone.utc),
    )


def test_ensure_data_directories_creates_data_and_episode_dirs(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path / "data", episodes_dir=tmp_path / "data/episodes")

    ensure_data_directories(storage)

    assert storage.data_dir.is_dir()
    assert storage.episodes_dir.is_dir()


def test_runtime_state_round_trip_through_temporary_directory(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    state = RuntimeState(
        station_status="playing",
        current_channel_id="sleep",
        current_podcast_id="history-extra",
        volume=70,
        previous_podcast_ids=("science-hour",),
        playback_supervisor_error=OutputError(
            code="backend_process_failed",
            message="Output process exited unexpectedly with code 1.",
        ),
    )

    store.save_runtime_state(state)

    assert store.load_runtime_state() == state


def test_missing_runtime_state_loads_default(tmp_path: Path) -> None:
    assert JsonStateStore(tmp_path).load_runtime_state() == RuntimeState()


def test_feed_metadata_round_trip(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    metadata = FeedMetadata(
        podcast_id="history-extra",
        feed_url="https://example.com/feed.xml",
        status="ok",
        last_checked_at=datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
        feed_title="History Extra",
        latest_episode=episode(),
        entry_count=3,
        playable_entry_count=2,
    )

    store.save_feed_metadata({metadata.podcast_id: metadata})

    assert store.load_feed_metadata() == {metadata.podcast_id: metadata}


def test_download_metadata_round_trip(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path)
    metadata = DownloadMetadata(
        podcast_id="history-extra",
        episode_identity="episode-guid",
        media_url="https://example.com/audio.mp3",
        media_type="audio/mpeg",
        local_file=tmp_path / "episodes/history-extra/audio.mp3",
        downloaded_at=datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
        title="New episode",
    )

    store.save_download_metadata({metadata.podcast_id: metadata})

    assert store.load_download_metadata() == {metadata.podcast_id: metadata}
