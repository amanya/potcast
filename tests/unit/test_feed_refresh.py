from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from potcast.feed_refresh import FeedRefreshService
from potcast.models import (
    ChannelConfig,
    DownloadMetadata,
    Episode,
    FeedMetadata,
    FeedParseError,
    FeedParseResult,
    PodcastConfig,
)


class MemoryFeedStore:
    def __init__(
        self,
        *,
        feeds: dict[str, FeedMetadata] | None = None,
        downloads: dict[str, DownloadMetadata] | None = None,
    ) -> None:
        self.feeds = feeds or {}
        self.downloads = downloads or {}
        self.saved_feed_metadata: list[dict[str, FeedMetadata]] = []
        self.saved_download_metadata: list[dict[str, DownloadMetadata]] = []

    def load_feed_metadata(self) -> dict[str, FeedMetadata]:
        return dict(self.feeds)

    def save_feed_metadata(self, metadata: dict[str, FeedMetadata]) -> None:
        self.feeds = dict(metadata)
        self.saved_feed_metadata.append(dict(metadata))

    def load_download_metadata(self) -> dict[str, DownloadMetadata]:
        return dict(self.downloads)

    def save_download_metadata(self, metadata: dict[str, DownloadMetadata]) -> None:
        self.downloads = dict(metadata)
        self.saved_download_metadata.append(dict(metadata))


class FakeFetcher:
    def __init__(self, content: bytes = b"<rss />", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.content


class FakeDownloader:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, DownloadMetadata | None]] = []

    def replace_episode(
        self,
        podcast_id: str,
        episode: Episode,
        previous: DownloadMetadata | None = None,
    ) -> DownloadMetadata:
        self.calls.append((podcast_id, episode.identity, previous))
        if self.error is not None:
            raise self.error
        return download(podcast_id, identity=episode.identity, title=episode.title)


def test_refresh_updates_feed_and_download_metadata_for_new_episode() -> None:
    store = MemoryFeedStore()
    fetcher = FakeFetcher()
    downloader = FakeDownloader()
    service = FeedRefreshService(
        channels(),
        store,
        fetcher,
        downloader,
        parser=lambda content: parse_ok(content, latest_episode=episode("episode-2")),
        clock=fixed_clock(),
    )

    result = service.refresh_now()

    assert result.accepted is True
    assert result.status.last_result == "ok"
    assert fetcher.urls == ["https://example.com/history-extra.xml"]
    assert downloader.calls == [("history-extra", "episode-2", None)]
    assert store.feeds["history-extra"].status == "ok"
    assert store.feeds["history-extra"].latest_episode is not None
    assert store.feeds["history-extra"].latest_episode.local_file == Path(
        "/data/episodes/history-extra/episode-2.mp3"
    )
    assert store.downloads["history-extra"].episode_identity == "episode-2"


def test_refresh_does_not_download_when_current_episode_is_unchanged() -> None:
    existing = download("history-extra", identity="episode-1")
    store = MemoryFeedStore(downloads={"history-extra": existing})
    downloader = FakeDownloader()
    service = FeedRefreshService(
        channels(),
        store,
        FakeFetcher(),
        downloader,
        parser=lambda content: parse_ok(content, latest_episode=episode("episode-1")),
        clock=fixed_clock(),
    )

    service.refresh_now()

    assert downloader.calls == []
    assert store.downloads["history-extra"] == existing
    assert store.feeds["history-extra"].status == "ok"


def test_feed_failure_preserves_previous_good_metadata_and_download() -> None:
    previous_episode = episode("episode-1")
    previous_feed = FeedMetadata(
        podcast_id="history-extra",
        feed_url="https://example.com/history-extra.xml",
        status="ok",
        feed_title="History Extra",
        latest_episode=previous_episode,
        entry_count=4,
        playable_entry_count=4,
    )
    existing_download = download("history-extra", identity="episode-1")
    store = MemoryFeedStore(
        feeds={"history-extra": previous_feed},
        downloads={"history-extra": existing_download},
    )
    service = FeedRefreshService(
        channels(),
        store,
        FakeFetcher(error=RuntimeError("network failed")),
        FakeDownloader(),
        parser=lambda content: parse_ok(content, latest_episode=episode("episode-2")),
        clock=fixed_clock(),
    )

    service.refresh_now()

    metadata = store.feeds["history-extra"]
    assert metadata.status == "feed_failed"
    assert metadata.latest_episode == previous_episode
    assert metadata.feed_title == "History Extra"
    assert metadata.error_code == "feed_fetch_failed"
    assert store.downloads["history-extra"] == existing_download


def test_parse_failure_preserves_previous_good_metadata_and_download() -> None:
    previous_episode = episode("episode-1")
    existing_download = download("history-extra", identity="episode-1")
    store = MemoryFeedStore(
        feeds={
            "history-extra": FeedMetadata(
                podcast_id="history-extra",
                feed_url="https://example.com/history-extra.xml",
                status="ok",
                latest_episode=previous_episode,
            )
        },
        downloads={"history-extra": existing_download},
    )
    service = FeedRefreshService(
        channels(),
        store,
        FakeFetcher(),
        FakeDownloader(),
        parser=lambda _content: FeedParseResult(
            ok=False,
            error=FeedParseError(code="malformed_feed", message="Broken feed."),
        ),
        clock=fixed_clock(),
    )

    service.refresh_now()

    assert store.feeds["history-extra"].status == "feed_failed"
    assert store.feeds["history-extra"].latest_episode == previous_episode
    assert store.feeds["history-extra"].error_code == "malformed_feed"
    assert store.downloads["history-extra"] == existing_download


def test_download_failure_preserves_previous_good_metadata_and_download() -> None:
    previous_episode = episode("episode-1")
    existing_download = download("history-extra", identity="episode-1")
    store = MemoryFeedStore(
        feeds={
            "history-extra": FeedMetadata(
                podcast_id="history-extra",
                feed_url="https://example.com/history-extra.xml",
                status="ok",
                latest_episode=previous_episode,
            )
        },
        downloads={"history-extra": existing_download},
    )
    service = FeedRefreshService(
        channels(),
        store,
        FakeFetcher(),
        FakeDownloader(error=RuntimeError("disk full")),
        parser=lambda content: parse_ok(content, latest_episode=episode("episode-2")),
        clock=fixed_clock(),
    )

    service.refresh_now()

    assert store.feeds["history-extra"].status == "download_failed"
    assert store.feeds["history-extra"].latest_episode == previous_episode
    assert store.feeds["history-extra"].error_code == "download_failed"
    assert store.downloads["history-extra"] == existing_download


def test_trigger_refresh_returns_before_background_download_and_rejects_overlap() -> None:
    tasks: list[object] = []

    def capture_task(task: object) -> None:
        tasks.append(task)

    downloader = FakeDownloader()
    store = MemoryFeedStore()
    service = FeedRefreshService(
        channels(),
        store,
        FakeFetcher(),
        downloader,
        parser=lambda content: parse_ok(content, latest_episode=episode("episode-2")),
        clock=fixed_clock(),
        background_starter=capture_task,
    )

    first = service.trigger_refresh()
    second = service.trigger_refresh()

    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "already_running"
    assert downloader.calls == []
    assert len(tasks) == 1

    task = tasks[0]
    assert callable(task)
    task()

    assert downloader.calls == [("history-extra", "episode-2", None)]
    assert service.status().running is False


def channels() -> tuple[ChannelConfig, ...]:
    return (
        ChannelConfig(
            id="sleep",
            name="Sleep",
            podcasts=(
                PodcastConfig(
                    id="history-extra",
                    name="History Extra",
                    feed_url="https://example.com/history-extra.xml",
                ),
            ),
        ),
    )


def episode(identity: str) -> Episode:
    return Episode(
        title=f"Episode {identity}",
        identity=identity,
        guid=identity,
        published_at=datetime(2026, 6, 9, 10, tzinfo=timezone.utc),
        media_url=f"https://cdn.example.com/{identity}.mp3",
        media_type="audio/mpeg",
    )


def download(
    podcast_id: str,
    *,
    identity: str,
    title: str | None = None,
) -> DownloadMetadata:
    return DownloadMetadata(
        podcast_id=podcast_id,
        episode_identity=identity,
        media_url=f"https://cdn.example.com/{identity}.mp3",
        media_type="audio/mpeg",
        local_file=Path(f"/data/episodes/{podcast_id}/{identity}.mp3"),
        downloaded_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        title=title or identity,
    )


def parse_ok(_content: bytes | str, *, latest_episode: Episode) -> FeedParseResult:
    return FeedParseResult(
        ok=True,
        latest_episode=latest_episode,
        feed_title="History Extra",
        entry_count=3,
        playable_entry_count=2,
    )


def fixed_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
