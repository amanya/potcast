"""Feed refresh orchestration and HTTP-backed fetch helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import httpx

from potcast.episodes import chronological, downloaded_episodes
from potcast.feeds import parse_feed
from potcast.models import (
    ChannelConfig,
    DownloadMetadata,
    Episode,
    FeedMetadata,
    FeedMonitorStatus,
    FeedParseResult,
    FeedRefreshSummary,
    FeedRefreshTriggerResult,
    PodcastConfig,
)

Clock = Callable[[], datetime]
FeedParser = Callable[[str | bytes], FeedParseResult]
BackgroundStarter = Callable[[Callable[[], None]], None]


class FeedMetadataStore(Protocol):
    """Persistence required by the feed refresh service."""

    def load_feed_metadata(self) -> dict[str, FeedMetadata]: ...

    def save_feed_metadata(self, metadata: dict[str, FeedMetadata]) -> None: ...

    def load_download_metadata(self) -> dict[str, DownloadMetadata]: ...

    def save_download_metadata(self, metadata: dict[str, DownloadMetadata]) -> None: ...


class FeedFetcher(Protocol):
    """Fetch RSS or Atom content for one configured podcast feed."""

    def fetch(self, url: str) -> bytes: ...


class EpisodeDownloader(Protocol):
    """Replace the local media file for one podcast episode."""

    def replace_episode(
        self,
        podcast_id: str,
        episode: Episode,
        previous: DownloadMetadata | None = None,
    ) -> DownloadMetadata: ...


class HttpxFeedFetcher:
    """HTTP feed fetcher with injectable timeout and user agent."""

    def __init__(self, *, timeout_seconds: int, user_agent: str) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch(self, url: str) -> bytes:
        response = httpx.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content


class HttpxDownloadWriter:
    """Downloader writer suitable for AtomicEpisodeDownloader."""

    def __init__(self, *, timeout_seconds: int, user_agent: str) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def __call__(self, url: str, path: Path) -> None:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            with path.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)


class FeedRefreshService:
    """Refresh configured feeds and download newly available episodes."""

    def __init__(
        self,
        channels: Sequence[ChannelConfig],
        state_store: FeedMetadataStore,
        fetcher: FeedFetcher,
        downloader: EpisodeDownloader,
        *,
        parser: FeedParser = parse_feed,
        clock: Clock | None = None,
        background_starter: BackgroundStarter | None = None,
        media_lock: threading.RLock | None = None,
        protected_episode: Callable[[], tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._media_lock = media_lock or threading.RLock()
        self._protected_episode = protected_episode or (lambda: (None, None))
        self.channels = tuple(channels)
        self.state_store = state_store
        self.fetcher = fetcher
        self.downloader = downloader
        self.parser = parser
        self._clock = clock or _utc_now
        self._background_starter = background_starter or _thread_starter
        self._lock = threading.Lock()
        self._monitor_status = FeedMonitorStatus()

    def trigger_refresh(self) -> FeedRefreshTriggerResult:
        """Start a refresh in the background unless one is already running."""

        if not self._begin_refresh():
            return FeedRefreshTriggerResult(
                accepted=False,
                reason="already_running",
                status=self.status(),
            )

        try:
            self._background_starter(self._run_started_refresh)
        except Exception as exc:
            self._finish_refresh(
                FeedRefreshSummary(
                    status="failed",
                    checked_count=0,
                    updated_count=0,
                    failed_count=1,
                ),
                error_code="refresh_start_failed",
                error_message=str(exc),
            )
            raise

        return FeedRefreshTriggerResult(accepted=True, status=self.status())

    def refresh_now(self) -> FeedRefreshTriggerResult:
        """Run one refresh synchronously; useful for tests and maintenance commands."""

        if not self._begin_refresh():
            return FeedRefreshTriggerResult(
                accepted=False,
                reason="already_running",
                status=self.status(),
            )
        self._run_started_refresh()
        return FeedRefreshTriggerResult(accepted=True, status=self.status())

    def status(self) -> FeedMonitorStatus:
        with self._lock:
            return self._monitor_status

    def feed_metadata(self) -> dict[str, FeedMetadata]:
        metadata = self.state_store.load_feed_metadata()
        for podcast in self._podcasts():
            metadata.setdefault(
                podcast.id,
                FeedMetadata(
                    podcast_id=podcast.id,
                    feed_url=podcast.feed_url,
                    status="unknown",
                ),
            )
        return metadata

    def _run_started_refresh(self) -> None:
        try:
            summary = self._refresh_all()
        except Exception as exc:
            self._finish_refresh(
                FeedRefreshSummary(
                    status="failed",
                    checked_count=0,
                    updated_count=0,
                    failed_count=1,
                ),
                error_code="refresh_failed",
                error_message=str(exc),
            )
            return

        self._finish_refresh(summary)

    def _refresh_all(self) -> FeedRefreshSummary:
        feed_metadata = self.state_store.load_feed_metadata()
        downloads = self.state_store.load_download_metadata()
        checked_count = 0
        updated_count = 0
        failed_count = 0

        for podcast in self._podcasts():
            checked_count += 1
            outcome = self._refresh_podcast(podcast, feed_metadata, downloads)
            if outcome == "updated":
                updated_count += 1
            elif outcome == "failed":
                failed_count += 1

            self.state_store.save_feed_metadata(feed_metadata)

        return FeedRefreshSummary(
            status="failed" if failed_count and failed_count == checked_count else "ok",
            checked_count=checked_count,
            updated_count=updated_count,
            failed_count=failed_count,
        )

    def _refresh_podcast(
        self,
        podcast: PodcastConfig,
        feed_metadata: dict[str, FeedMetadata],
        downloads: dict[str, DownloadMetadata],
    ) -> str:
        checked_at = self._clock()
        previous_feed = feed_metadata.get(podcast.id)
        previous_download = downloads.get(podcast.id)

        try:
            content = self.fetcher.fetch(podcast.feed_url)
        except Exception as exc:
            feed_metadata[podcast.id] = _failed_feed_metadata(
                podcast,
                previous_feed,
                checked_at,
                status="feed_failed",
                code="feed_fetch_failed",
                message=str(exc),
            )
            return "failed"

        result = self.parser(content)
        if not result.ok:
            error = result.error
            feed_metadata[podcast.id] = _failed_feed_metadata(
                podcast,
                previous_feed,
                checked_at,
                status="feed_failed",
                code=error.code if error is not None else "feed_parse_failed",
                message=error.message if error is not None else "Feed could not be parsed.",
            )
            return "failed"

        episode = result.latest_episode
        if episode is None:
            feed_metadata[podcast.id] = FeedMetadata(
                podcast_id=podcast.id,
                feed_url=podcast.feed_url,
                status="no_playable_episode",
                last_checked_at=checked_at,
                feed_title=result.feed_title,
                latest_episode=None,
                entry_count=result.entry_count,
                playable_entry_count=result.playable_entry_count,
            )
            return "unchanged"

        days = self._retention_days(podcast)
        cutoff = checked_at - timedelta(days=days) if days is not None else None
        candidates = {
            e.identity: e
            for e in (result.episodes or (episode,))
            if (
                e.identity == episode.identity
                or (cutoff is not None and e.published_at is not None and e.published_at >= cutoff)
            )
        }
        cached = (
            {e.identity: e for e in downloaded_episodes(previous_download)}
            if previous_download is not None
            else {}
        )
        available = dict(cached)
        staged: list[Episode] = []
        try:
            for identity, candidate in candidates.items():
                existing = available.get(identity)
                if existing is not None and existing.local_file is not None:
                    # Feed dates can repair legacy metadata without downloading again.
                    available[identity] = replace(existing, published_at=candidate.published_at)
                    continue
                download = self.downloader.replace_episode(podcast.id, candidate)
                ready = _episode_with_download(candidate, download)
                available[identity] = ready
                staged.append(ready)
        except Exception as exc:
            for ready in staged:
                if ready.local_file is not None:
                    _remove_obsolete(ready.local_file)
            feed_metadata[podcast.id] = _failed_feed_metadata(
                podcast,
                previous_feed,
                checked_at,
                status="download_failed",
                code="download_failed",
                message=str(exc),
            )
            return "failed"

        latest = available[episode.identity]
        # A truncated feed must not replace a newer, already downloaded episode.
        if previous_download is not None and previous_download.published_at is not None:
            previous_latest = available[previous_download.episode_identity]
            if latest.published_at is None or previous_download.published_at > latest.published_at:
                latest = previous_latest

        with self._media_lock:
            protected_podcast, protected_identity = self._protected_episode()
            kept = tuple(
                e
                for e in available.values()
                if (
                    e.identity == latest.identity
                    or (podcast.id == protected_podcast and e.identity == protected_identity)
                    or (
                        cutoff is not None
                        and e.published_at is not None
                        and e.published_at >= cutoff
                    )
                )
            )
            assert latest.local_file is not None and latest.downloaded_at is not None
            downloads[podcast.id] = DownloadMetadata(
                podcast_id=podcast.id,
                episode_identity=latest.identity,
                media_url=latest.media_url,
                media_type=latest.media_type,
                local_file=latest.local_file,
                downloaded_at=latest.downloaded_at,
                title=latest.title,
                published_at=latest.published_at,
                retained_episodes=chronological(
                    tuple(e for e in kept if e.identity != latest.identity)
                ),
            )
            # Commit metadata before deleting files, while selection is excluded.
            self.state_store.save_download_metadata(downloads)
            kept_ids = {e.identity for e in kept}
            for identity, obsolete in cached.items():
                if identity not in kept_ids and obsolete.local_file is not None:
                    _remove_obsolete(obsolete.local_file)
        feed_metadata[podcast.id] = _ok_feed_metadata(podcast, result, checked_at, latest)
        return "updated" if staged else "unchanged"

    def _retention_days(self, podcast: PodcastConfig) -> int | None:
        if podcast.retention_days is not None:
            return podcast.retention_days
        for channel in self.channels:
            if podcast in channel.podcasts:
                return channel.retention_days
        return None

    def _begin_refresh(self) -> bool:
        with self._lock:
            if self._monitor_status.running:
                return False
            self._monitor_status = replace(
                self._monitor_status,
                running=True,
                last_started_at=self._clock(),
                last_result=None,
                last_error_code=None,
                last_error_message=None,
            )
            return True

    def _finish_refresh(
        self,
        summary: FeedRefreshSummary,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            self._monitor_status = replace(
                self._monitor_status,
                running=False,
                last_finished_at=self._clock(),
                last_result=summary.status,
                last_error_code=error_code,
                last_error_message=error_message,
            )

    def _podcasts(self) -> tuple[PodcastConfig, ...]:
        return tuple(podcast for channel in self.channels for podcast in channel.podcasts)


def _ok_feed_metadata(
    podcast: PodcastConfig,
    result: FeedParseResult,
    checked_at: datetime,
    episode: Episode,
) -> FeedMetadata:
    return FeedMetadata(
        podcast_id=podcast.id,
        feed_url=podcast.feed_url,
        status="ok",
        last_checked_at=checked_at,
        feed_title=result.feed_title,
        latest_episode=episode,
        entry_count=result.entry_count,
        playable_entry_count=result.playable_entry_count,
    )


def _failed_feed_metadata(
    podcast: PodcastConfig,
    previous: FeedMetadata | None,
    checked_at: datetime,
    *,
    status: str,
    code: str,
    message: str,
) -> FeedMetadata:
    return FeedMetadata(
        podcast_id=podcast.id,
        feed_url=podcast.feed_url,
        status=status,
        last_checked_at=checked_at,
        feed_title=previous.feed_title if previous is not None else None,
        latest_episode=previous.latest_episode if previous is not None else None,
        entry_count=previous.entry_count if previous is not None else 0,
        playable_entry_count=previous.playable_entry_count if previous is not None else 0,
        error_code=code,
        error_message=message,
    )


def _episode_with_download(episode: Episode, download: DownloadMetadata) -> Episode:
    return replace(
        episode,
        local_file=download.local_file,
        downloaded_at=download.downloaded_at,
    )


def _thread_starter(target: Callable[[], None]) -> None:
    thread = threading.Thread(target=target, name="potcast-feed-refresh", daemon=True)
    thread.start()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _remove_obsolete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logging.getLogger(__name__).warning("Could not remove obsolete episode: %s", path)
