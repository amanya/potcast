"""Episode collections and chronological rotation without infrastructure dependencies."""

from __future__ import annotations

from datetime import datetime, timezone

from potcast.models import DownloadMetadata, Episode


def downloaded_episodes(download: DownloadMetadata) -> tuple[Episode, ...]:
    latest = Episode(
        title=download.title or download.episode_identity,
        identity=download.episode_identity,
        guid=None,
        published_at=download.published_at,
        media_url=download.media_url,
        media_type=download.media_type,
        local_file=download.local_file,
        downloaded_at=download.downloaded_at,
    )
    return (latest, *download.retained_episodes)


def chronological(episodes: tuple[Episode, ...]) -> tuple[Episode, ...]:
    return tuple(
        sorted(
            episodes,
            key=lambda e: (
                e.published_at or e.downloaded_at or datetime.min.replace(tzinfo=timezone.utc),
                e.identity,
            ),
        )
    )


def next_episode(episodes: tuple[Episode, ...], previous_identity: str | None) -> Episode:
    ordered = chronological(episodes)
    for index, episode in enumerate(ordered):
        if episode.identity == previous_identity:
            return ordered[(index + 1) % len(ordered)]
    return ordered[0]
