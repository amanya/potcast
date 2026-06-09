"""RSS feed parsing and newest playable episode selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any, cast

import feedparser  # type: ignore[import-untyped]

from potcast.models import Episode, FeedParseError, FeedParseResult

SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
        "audio/ogg",
    }
)


def parse_feed(content: str | bytes) -> FeedParseResult:
    """Parse RSS or Atom content and select the newest playable episode."""

    parsed = feedparser.parse(content)
    feed = _mapping(parsed.get("feed", {}))
    entries = tuple(_iter_mappings(parsed.get("entries", ())))

    if bool(parsed.get("bozo")) and not entries:
        exception = parsed.get("bozo_exception")
        return FeedParseResult(
            ok=False,
            error=FeedParseError(
                code="malformed_feed",
                message=_malformed_message(exception),
            ),
            feed_title=_optional_text(feed.get("title")),
            entry_count=0,
            playable_entry_count=0,
        )

    playable = tuple(
        episode
        for episode in (_episode_from_entry(entry) for entry in entries)
        if episode is not None
    )

    latest_episode = max(playable, key=_episode_sort_key, default=None)
    return FeedParseResult(
        ok=True,
        latest_episode=latest_episode,
        feed_title=_optional_text(feed.get("title")),
        entry_count=len(entries),
        playable_entry_count=len(playable),
    )


def _episode_from_entry(entry: Mapping[str, Any]) -> Episode | None:
    enclosure = _first_playable_enclosure(entry)
    if enclosure is None:
        return None

    media_url = _optional_text(enclosure.get("href")) or _optional_text(enclosure.get("url"))
    media_type = _optional_text(enclosure.get("type"))
    if media_url is None or media_type is None:
        return None

    guid = _entry_guid(entry)
    identity = guid or media_url
    title = _optional_text(entry.get("title")) or media_url

    return Episode(
        title=title,
        identity=identity,
        guid=guid,
        published_at=_entry_datetime(entry),
        media_url=media_url,
        media_type=media_type,
        duration=_entry_duration(entry),
    )


def _first_playable_enclosure(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for enclosure in _entry_enclosures(entry):
        media_type = _optional_text(enclosure.get("type"))
        if media_type in SUPPORTED_MEDIA_TYPES:
            return enclosure
    return None


def _entry_enclosures(entry: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield from _iter_mappings(entry.get("enclosures", ()))

    for link in _iter_mappings(entry.get("links", ())):
        rel = _optional_text(link.get("rel"))
        if rel == "enclosure":
            yield link


def _entry_guid(entry: Mapping[str, Any]) -> str | None:
    for key in ("id", "guid"):
        value = _optional_text(entry.get(key))
        if value is not None:
            return value
    return None


def _entry_datetime(entry: Mapping[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if isinstance(value, struct_time):
            return datetime(*value[:6], tzinfo=timezone.utc)

    for key in ("published", "updated", "created"):
        value = _optional_text(entry.get(key))
        if value is not None:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

    return None


def _entry_duration(entry: Mapping[str, Any]) -> str | None:
    duration = _optional_text(entry.get("itunes_duration"))
    if duration is not None:
        return duration

    tags = entry.get("tags", ())
    for tag in _iter_mappings(tags):
        if _optional_text(tag.get("term")) == "duration":
            return _optional_text(tag.get("label"))

    return None


def _episode_sort_key(episode: Episode) -> datetime:
    return episode.published_at or datetime.min.replace(tzinfo=timezone.utc)


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | Mapping):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    return {}


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _malformed_message(exception: Any) -> str:
    if exception is None:
        return "Feed could not be parsed."
    return f"Feed could not be parsed: {exception}"
