from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from potcast.downloader import AtomicEpisodeDownloader, final_episode_path
from potcast.errors import DownloadError
from potcast.models import DownloadMetadata, Episode


def episode(
    *,
    identity: str = "episode-guid",
    media_url: str = "https://cdn.example.com/media/audio.mp3?token=secret",
    media_type: str = "audio/mpeg",
) -> Episode:
    return Episode(
        title="New episode",
        identity=identity,
        guid=identity,
        published_at=datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        media_url=media_url,
        media_type=media_type,
    )


def metadata(path: Path, *, identity: str = "old-guid") -> DownloadMetadata:
    return DownloadMetadata(
        podcast_id="history-extra",
        episode_identity=identity,
        media_url="https://cdn.example.com/old.mp3",
        media_type="audio/mpeg",
        local_file=path,
        downloaded_at=datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        title="Old episode",
    )


def test_new_episode_replaces_old_episode(tmp_path: Path) -> None:
    episodes_dir = tmp_path / "episodes"
    old_path = episodes_dir / "history-extra" / "old.mp3"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old audio")

    downloader = AtomicEpisodeDownloader(
        episodes_dir,
        writer=lambda _url, path: path.write_bytes(b"new audio"),
        clock=lambda: datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
    )

    result = downloader.replace_episode("history-extra", episode(), previous=metadata(old_path))

    assert result.local_file.read_bytes() == b"new audio"
    assert result.episode_identity == "episode-guid"
    assert old_path.exists() is False


def test_failed_download_preserves_old_episode(tmp_path: Path) -> None:
    episodes_dir = tmp_path / "episodes"
    old_path = episodes_dir / "history-extra" / "old.mp3"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old audio")

    def fail(_url: str, _path: Path) -> None:
        raise RuntimeError("network failed")

    downloader = AtomicEpisodeDownloader(episodes_dir, writer=fail)

    with pytest.raises(DownloadError):
        downloader.replace_episode("history-extra", episode(), previous=metadata(old_path))

    assert old_path.read_bytes() == b"old audio"
    assert list(old_path.parent.glob("*.tmp")) == []


def test_empty_download_is_rejected(tmp_path: Path) -> None:
    episodes_dir = tmp_path / "episodes"
    old_path = episodes_dir / "history-extra" / "old.mp3"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old audio")

    downloader = AtomicEpisodeDownloader(
        episodes_dir,
        writer=lambda _url, path: path.write_bytes(b""),
    )

    with pytest.raises(DownloadError, match="empty"):
        downloader.replace_episode("history-extra", episode(), previous=metadata(old_path))

    assert old_path.read_bytes() == b"old audio"


def test_final_filename_is_stable_and_safe(tmp_path: Path) -> None:
    unsafe_episode = episode(
        identity="https://example.com/shows/Sleep Story?episode=1&name=Moon",
        media_url="https://cdn.example.com/files/audio?id=1",
        media_type="audio/ogg",
    )

    first = final_episode_path(tmp_path, "History Extra: Sleep/Stories", unsafe_episode)
    second = final_episode_path(tmp_path, "History Extra: Sleep/Stories", unsafe_episode)

    assert first == second
    assert first.parent == tmp_path / "history-extra-sleep-stories"
    assert first.name.startswith("history-extra-sleep-stories-")
    assert first.suffix == ".ogg"
    assert ":" not in first.name
    assert "/" not in first.name
