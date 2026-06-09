from __future__ import annotations

from datetime import datetime, timezone

from potcast.feeds import parse_feed


def rss_with_items(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fixture Feed</title>
    {items}
  </channel>
</rss>
"""


def item(
    *,
    title: str,
    url: str = "https://example.com/audio.mp3",
    media_type: str = "audio/mpeg",
    guid: str | None = "episode-guid",
    published: str = "Mon, 01 Jan 2024 10:00:00 GMT",
) -> str:
    guid_xml = f"<guid>{guid}</guid>" if guid is not None else ""
    return f"""
<item>
  <title>{title}</title>
  {guid_xml}
  <pubDate>{published}</pubDate>
  <enclosure url="{url}" type="{media_type}" length="1234" />
</item>
"""


def test_selects_newest_playable_episode() -> None:
    result = parse_feed(
        rss_with_items(
            item(
                title="Older",
                guid="older-guid",
                url="https://example.com/older.mp3",
                published="Mon, 01 Jan 2024 10:00:00 GMT",
            )
            + item(
                title="Newer",
                guid="newer-guid",
                url="https://example.com/newer.mp3",
                published="Tue, 02 Jan 2024 10:00:00 GMT",
            )
        )
    )

    assert result.ok is True
    assert result.latest_episode is not None
    assert result.latest_episode.title == "Newer"
    assert result.latest_episode.identity == "newer-guid"
    assert result.latest_episode.published_at == datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    assert result.entry_count == 2
    assert result.playable_entry_count == 2


def test_uses_guid_identity_when_available() -> None:
    result = parse_feed(
        rss_with_items(
            item(
                title="Guided",
                guid="stable-guid",
                url="https://cdn.example.com/episode.mp3",
            )
        )
    )

    assert result.latest_episode is not None
    assert result.latest_episode.guid == "stable-guid"
    assert result.latest_episode.identity == "stable-guid"


def test_falls_back_to_enclosure_url_identity() -> None:
    result = parse_feed(
        rss_with_items(
            item(
                title="URL identity",
                guid=None,
                url="https://cdn.example.com/episode.mp3",
            )
        )
    )

    assert result.latest_episode is not None
    assert result.latest_episode.guid is None
    assert result.latest_episode.identity == "https://cdn.example.com/episode.mp3"


def test_skips_unsupported_media() -> None:
    result = parse_feed(
        rss_with_items(
            item(
                title="Video",
                url="https://example.com/video.mp4",
                media_type="video/mp4",
            )
        )
    )

    assert result.ok is True
    assert result.latest_episode is None
    assert result.entry_count == 1
    assert result.playable_entry_count == 0


def test_skips_entries_without_enclosures() -> None:
    result = parse_feed(
        rss_with_items(
            """
<item>
  <title>No enclosure</title>
  <guid>no-enclosure</guid>
  <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
</item>
"""
        )
    )

    assert result.ok is True
    assert result.latest_episode is None
    assert result.entry_count == 1
    assert result.playable_entry_count == 0


def test_handles_malformed_feeds_with_structured_failure_result() -> None:
    result = parse_feed(b"\x81\x82not xml")

    assert result.ok is False
    assert result.latest_episode is None
    assert result.error is not None
    assert result.error.code == "malformed_feed"
    assert result.playable_entry_count == 0
