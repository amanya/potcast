from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

import pytest

from potcast.app import AppServices, create_app
from potcast.config import parse_config
from potcast.downloader import AtomicEpisodeDownloader
from potcast.episodes import downloaded_episodes
from potcast.errors import ConfigError
from potcast.feed_refresh import FeedRefreshService
from potcast.feeds import parse_feed
from potcast.models import ChannelConfig, PodcastConfig, StationConfig
from potcast.outputs.base import FakeOutputBackend
from potcast.service import StationService
from potcast.state import JsonStateStore

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "retention.xml"


def configuration(channel_days=None, podcast_days=None):
    return parse_config(
        {
            "channels": [
                {
                    "id": "history",
                    "name": "History",
                    "retention_days": channel_days,
                    "podcasts": [
                        {
                            "id": "a",
                            "name": "A",
                            "feed_url": "https://example.com/rss",
                            "retention_days": podcast_days,
                        }
                    ],
                }
            ]
        }
    )


class Fetcher:
    content = FIXTURE.read_bytes()
    error = False

    def fetch(self, url):
        if self.error:
            raise RuntimeError("offline")
        return self.content


class Harness:
    def __init__(self, path, channel_days=14, podcast_days=None):
        self.config = configuration(channel_days, podcast_days)
        self.store = JsonStateStore(path)
        self.fetcher = Fetcher()
        self.now = NOW
        self.urls = []
        self.fail_url = None
        self.lock = RLock()
        self.output = FakeOutputBackend()
        self.station = StationService(
            self.config.channels,
            StationConfig(shuffle_podcasts=False),
            self.store,
            self.output,
            media_lock=self.lock,
        )
        self.refresh = FeedRefreshService(
            self.config.channels,
            self.store,
            self.fetcher,
            AtomicEpisodeDownloader(path / "episodes", self.write, clock=lambda: self.now),
            clock=lambda: self.now,
            media_lock=self.lock,
            protected_episode=lambda: (
                self.store.load_runtime_state().current_podcast_id,
                self.store.load_runtime_state().current_episode_identity,
            ),
        )

    def write(self, url, path):
        self.urls.append(url)
        if url == self.fail_url:
            raise RuntimeError("download failed")
        path.write_bytes(b"audio")

    def episodes(self):
        return downloaded_episodes(self.store.load_download_metadata()["a"])


def test_retention_config_defaults_and_overrides():
    assert configuration().channels[0].retention_days is None
    config = configuration(14, 7)
    assert config.channels[0].retention_days == 14
    assert config.channels[0].podcasts[0].retention_days == 7


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "14", [], {}])
@pytest.mark.parametrize("scope", ["channel", "podcast"])
def test_invalid_retention(value, scope):
    with pytest.raises(ConfigError):
        configuration(value if scope == "channel" else 14, value if scope == "podcast" else None)


def test_fixture_deduplicates_supported_episodes_and_selects_latest():
    result = parse_feed(FIXTURE.read_bytes())
    assert result.latest_episode.identity == "new"
    assert {e.identity for e in result.episodes} == {
        "new",
        "middle",
        "boundary",
        "expired",
        "undated",
    }


@pytest.mark.parametrize(
    "channel_days,podcast_days,expected",
    [
        (None, None, {"new"}),
        (14, None, {"new", "middle", "boundary"}),
        (14, 7, {"new", "middle"}),
        (7, 14, {"new", "middle", "boundary"}),
    ],
)
def test_backfill_window_override_boundary_and_no_redownload(
    tmp_path,
    channel_days,
    podcast_days,
    expected,
):
    h = Harness(tmp_path, channel_days, podcast_days)
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == expected
    assert all(e.local_file.read_bytes() == b"audio" for e in h.episodes())
    calls = list(h.urls)
    h.refresh.refresh_now()
    assert h.urls == calls
    assert len(h.urls) == len(expected)


def test_cleanup_preserves_latest_and_selected_then_removes_expired(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    first = h.station.play()
    assert first.status.active_episode.identity == "boundary"
    paths = {e.identity: e.local_file for e in h.episodes()}
    h.now += timedelta(days=30)
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new", "boundary"}
    assert paths["boundary"].exists()
    assert not paths["middle"].exists()
    assert h.station.status().active_episode.identity == "boundary"
    h.station.next()
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new"}
    assert not paths["boundary"].exists()


def test_feed_absence_does_not_drop_retained_downloads(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    h.fetcher.content = b"""<rss><channel><item><guid>new</guid>
    <pubDate>Fri, 16 Oct 2026 12:00:00 GMT</pubDate>
    <enclosure url="https://example.com/new.mp3" type="audio/mpeg"/>
    </item></channel></rss>"""
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new", "middle", "boundary"}


@pytest.mark.parametrize("failure", ["fetch", "parse", "download"])
def test_failed_refresh_preserves_collection_and_files(tmp_path, failure):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    before = h.store.load_download_metadata()
    h.now += timedelta(days=30)
    if failure == "fetch":
        h.fetcher.error = True
    elif failure == "parse":
        h.fetcher.content = b"not xml"
    else:
        h.fetcher.content = h.fetcher.content.replace(
            b"<channel>",
            b"""<channel>
        <item><guid>future</guid><pubDate>Sun, 15 Nov 2026 12:00:00 GMT</pubDate>
        <enclosure url="https://example.com/future.mp3" type="audio/mpeg" /></item>
        <item><guid>failure</guid><pubDate>Sat, 14 Nov 2026 12:00:00 GMT</pubDate>
        <enclosure url="https://example.com/failure.mp3" type="audio/mpeg" /></item>""",
        )
        h.fail_url = "https://example.com/failure.mp3"
    h.refresh.refresh_now()
    assert h.store.load_download_metadata() == before
    assert all(e.local_file.exists() for e in h.episodes())
    assert h.store.load_feed_metadata()["a"].status.endswith("failed")
    if failure == "download":
        assert len(list((tmp_path / "episodes").rglob("*.mp3"))) == 3


def test_round_robin_episode_rotation_completion_restart_and_idempotence(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    original = h.store.load_download_metadata()["a"]
    h.store.save_download_metadata(
        {
            "a": original,
            "b": replace(
                original,
                podcast_id="b",
                episode_identity="b-new",
                retained_episodes=(),
            ),
        }
    )
    channels = (
        ChannelConfig(
            "history",
            "History",
            (
                PodcastConfig("a", "A", "https://example.com/a"),
                PodcastConfig("b", "B", "https://example.com/b"),
            ),
        ),
    )
    station = StationService(channels, StationConfig(shuffle_podcasts=False), h.store, h.output)
    assert station.play().status.active_episode.identity == "boundary"
    assert station.play().status.active_episode.identity == "boundary"
    station.pause()
    assert station.play().status.active_episode.identity == "boundary"
    h.output.finish_current_episode()
    assert station.advance_if_finished().status.active_episode.identity == "b-new"
    assert station.next().status.active_episode.identity == "middle"
    station = StationService(channels, StationConfig(shuffle_podcasts=False), h.store, h.output)
    assert station.play().status.active_episode.identity == "middle"
    assert station.next().status.active_episode.identity == "b-new"
    assert station.next().status.active_episode.identity == "new"
    station.next()
    assert station.next().status.active_episode.identity == "boundary"


def test_legacy_json_loads_without_new_fields(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    raw = json.loads((tmp_path / "downloads.json").read_text())
    raw["a"].pop("retained_episodes")
    raw["a"].pop("published_at")
    (tmp_path / "downloads.json").write_text(json.dumps(raw))
    (tmp_path / "state.json").write_text('{"current_podcast_id":"a","previous_podcast_ids":[]}')
    assert h.store.load_runtime_state().episode_cursors == {}
    assert h.store.load_runtime_state().current_episode_identity is None
    assert h.store.load_download_metadata()["a"].retained_episodes == ()
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new", "middle", "boundary"}


def test_http_status_reports_selected_episode_after_refresh(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    app = create_app(config=h.config, services=AppServices(station=h.station, feeds=h.refresh))
    client = app.test_client()
    assert client.get("/play").json["status"]["active_episode"]["identity"] == "boundary"
    h.now += timedelta(days=30)
    h.refresh.refresh_now()
    assert client.get("/status").json["status"]["active_episode"]["identity"] == "boundary"
    assert client.get("/next").json["status"]["active_episode"]["identity"] == "new"


def test_removing_retention_cleans_up_except_current_selection(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    h.station.play()
    h.station.pause()
    h.refresh.channels = configuration().channels
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new", "boundary"}
    assert h.station.play().status.active_episode.identity == "boundary"
    h.station.next()
    h.refresh.refresh_now()
    assert {e.identity for e in h.episodes()} == {"new"}


def test_selection_during_download_is_protected_at_commit(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    selected = []

    def write_and_select(url, path):
        # Commands remain available while the feed service downloads outside its lock.
        selected.append(h.station.play().status.active_episode)
        h.write(url, path)

    h.refresh.downloader = AtomicEpisodeDownloader(
        tmp_path / "episodes",
        write_and_select,
        clock=lambda: h.now,
    )
    h.now += timedelta(days=30)
    h.fetcher.content = h.fetcher.content.replace(
        b"<channel>",
        b"""<channel>
    <item><guid>future</guid><pubDate>Sun, 15 Nov 2026 12:00:00 GMT</pubDate>
    <enclosure url="https://example.com/future.mp3" type="audio/mpeg"/></item>""",
    )
    h.refresh.refresh_now()
    assert selected[0].identity == "boundary"
    assert selected[0].local_file.exists()
    assert h.station.status().active_episode.identity == "boundary"
    assert {e.identity for e in h.episodes()} == {"boundary", "future"}


def test_shuffle_keeps_episode_rotation_per_program(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    original = h.store.load_download_metadata()["a"]
    h.store.save_download_metadata(
        {
            "a": original,
            "b": replace(
                original,
                podcast_id="b",
                episode_identity="b-new",
                retained_episodes=(),
            ),
        }
    )
    channels = (
        ChannelConfig(
            "history",
            "History",
            (
                PodcastConfig("a", "A", "https://example.com/a"),
                PodcastConfig("b", "B", "https://example.com/b"),
            ),
        ),
    )
    station = StationService(channels, StationConfig(shuffle_podcasts=True), h.store, h.output)
    assert station.play().status.active_episode.identity == "boundary"
    assert station.next().status.active_episode.identity == "b-new"
    assert station.next().status.active_episode.identity == "middle"
    assert station.previous().status.active_episode.identity == "b-new"


def test_channel_navigation_and_direct_selection_rotate_program_episodes(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    original = h.store.load_download_metadata()["a"]
    h.store.save_download_metadata(
        {
            "a": original,
            "b": replace(
                original,
                podcast_id="b",
                episode_identity="b-new",
                retained_episodes=(),
            ),
        }
    )
    channels = (
        ChannelConfig("history", "History", (PodcastConfig("a", "A", "https://a"),)),
        ChannelConfig("humor", "Humor", (PodcastConfig("b", "B", "https://b"),)),
    )
    station = StationService(channels, StationConfig(), h.store, h.output)
    assert station.play().status.active_episode.identity == "boundary"
    assert station.next_channel().status.active_episode.identity == "b-new"
    assert station.previous_channel().status.active_episode.identity == "middle"
    assert station.select_podcast("a").status.active_episode.identity == "new"
    assert station.select_channel("history").status.active_episode.identity == "boundary"


def test_backend_failure_recovery_retries_same_episode(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    h.station.play()
    h.output.fail("backend_process_failed", "broken")
    h.station.advance_if_finished()
    assert h.station.recover_output().status.active_episode.identity == "boundary"
    assert h.station.next().status.active_episode.identity == "middle"


def test_empty_feed_preserves_previous_collection(tmp_path):
    h = Harness(tmp_path)
    h.refresh.refresh_now()
    before = h.store.load_download_metadata()
    h.now += timedelta(days=30)
    h.fetcher.content = b"<rss><channel><title>Empty</title></channel></rss>"
    h.refresh.refresh_now()
    assert h.store.load_download_metadata() == before
    assert all(e.local_file.exists() for e in h.episodes())


def test_undated_feed_keeps_one_latest_episode(tmp_path):
    h = Harness(tmp_path)
    h.fetcher.content = b"""<rss><channel>
    <item><guid>first</guid>
    <enclosure url="https://example.com/first.mp3" type="audio/mpeg"/></item>
    <item><guid>second</guid>
    <enclosure url="https://example.com/second.mp3" type="audio/mpeg"/></item>
    </channel></rss>"""
    h.refresh.refresh_now()
    assert [e.identity for e in h.episodes()] == ["first"]


def test_new_download_does_not_interrupt_active_latest_only_episode(tmp_path):
    h = Harness(tmp_path, channel_days=None)
    h.refresh.refresh_now()
    h.station.play()
    original_path = h.episodes()[0].local_file
    h.fetcher.content = h.fetcher.content.replace(
        b"<channel>",
        b"""<channel>
    <item><guid>future</guid><pubDate>Sun, 18 Oct 2026 12:00:00 GMT</pubDate>
    <enclosure url="https://example.com/future.mp3" type="audio/mpeg"/></item>""",
    )
    h.refresh.refresh_now()
    assert h.station.status().active_episode.identity == "new"
    assert h.station.play().status.active_episode.identity == "new"
    assert original_path.exists()
    assert h.station.next().status.active_episode.identity == "future"
    h.refresh.refresh_now()
    assert not original_path.exists()
