from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask.testing import FlaskClient

from potcast.app import AppServices, create_app
from potcast.models import (
    ChannelConfig,
    CommandError,
    Episode,
    FeedMetadata,
    FeedMonitorStatus,
    FeedRefreshTriggerResult,
    OutputError,
    OutputStatus,
    PlaybackSupervisorStatus,
    PodcastConfig,
    StationCommandResult,
    StationStatus,
)


class SpyStationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.volume = 70
        self.errors: dict[str, CommandError] = {}
        self.status_override: StationStatus | None = None

    def play(self) -> StationCommandResult:
        return self._command("play")

    def pause(self) -> StationCommandResult:
        return self._command("pause")

    def toggle(self) -> StationCommandResult:
        return self._command("toggle")

    def stop(self) -> StationCommandResult:
        return self._command("stop")

    def next(self) -> StationCommandResult:
        return self._command("next")

    def previous(self) -> StationCommandResult:
        return self._command("previous")

    def next_channel(self) -> StationCommandResult:
        return self._command("next_channel")

    def previous_channel(self) -> StationCommandResult:
        return self._command("previous_channel")

    def select_channel(self, channel_id: str) -> StationCommandResult:
        return self._command("select_channel", channel_id)

    def select_podcast(self, podcast_id: str) -> StationCommandResult:
        return self._command("select_podcast", podcast_id)

    def set_volume(self, volume: int) -> StationCommandResult:
        self.volume = volume
        return self._command("set_volume", volume)

    def recover_output(self) -> StationCommandResult:
        return self._command("recover_output")

    def status(self) -> StationStatus:
        self.calls.append(("status", None))
        if self.status_override is not None:
            return self.status_override
        return _status(volume=self.volume)

    def _command(self, name: str, arg: object | None = None) -> StationCommandResult:
        self.calls.append((name, arg))
        error = self.errors.get(name)
        return StationCommandResult(
            ok=error is None,
            status=_status(volume=self.volume),
            error=error,
        )


class SpyFeedService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.accept_refresh = True
        self.running = False

    def trigger_refresh(self) -> FeedRefreshTriggerResult:
        self.calls.append("trigger_refresh")
        if not self.accept_refresh:
            return FeedRefreshTriggerResult(
                accepted=False,
                reason="already_running",
                status=self.status(),
            )
        self.running = True
        return FeedRefreshTriggerResult(accepted=True, status=self.status())

    def status(self) -> FeedMonitorStatus:
        self.calls.append("status")
        return FeedMonitorStatus(
            running=self.running,
            last_started_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
            last_finished_at=None,
            last_result=None,
        )

    def feed_metadata(self) -> dict[str, FeedMetadata]:
        self.calls.append("feed_metadata")
        return {
            "history-extra": FeedMetadata(
                podcast_id="history-extra",
                feed_url="https://example.com/history-extra.xml",
                status="ok",
                last_checked_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
                feed_title="History Extra",
                latest_episode=_status(volume=70).active_episode,
                entry_count=2,
                playable_entry_count=1,
            )
        }


def client_and_service() -> tuple[FlaskClient, SpyStationService]:
    service = SpyStationService()
    app = create_app(services=AppServices(station=service))
    return app.test_client(), service


def client_and_services() -> tuple[FlaskClient, SpyStationService, SpyFeedService]:
    station = SpyStationService()
    feeds = SpyFeedService()
    app = create_app(services=AppServices(station=station, feeds=feeds))
    return app.test_client(), station, feeds


def test_health_returns_version() -> None:
    client, _service = client_and_service()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "version": "0.1.0"}


def test_status_returns_station_status_json() -> None:
    client, service = client_and_service()

    response = client.get("/status")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"]["station_state"] == "playing"
    assert payload["status"]["active_channel"]["id"] == "sleep"
    assert payload["status"]["active_podcast"]["id"] == "history-extra"
    assert payload["status"]["active_episode"]["local_file"] == "/data/history-extra.mp3"
    assert payload["status"]["active_episode"]["downloaded_at"] == "2026-06-09T12:00:00+00:00"
    assert payload["status"]["output"]["backend"] == "fake"
    assert service.calls == [("status", None)]


def test_status_serializes_structured_output_error() -> None:
    client, service = client_and_service()
    service.status_override = _status(volume=70, output_error_code="backend_process_failed")

    response = client.get("/status")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"]["station_state"] == "idle"
    assert payload["status"]["output"]["state"] == "error"
    assert payload["status"]["output"]["connected"] is False
    assert payload["status"]["output"]["error"] == {
        "code": "backend_process_failed",
        "message": "Output process exited unexpectedly with code 1.",
    }
    assert payload["status"]["playback_supervisor"] == {
        "state": "blocked",
        "last_error": {
            "code": "backend_process_failed",
            "message": "Output process exited unexpectedly with code 1.",
        },
        "next_retry_at": None,
        "retry_attempts": 0,
        "max_retry_attempts": 0,
    }


def test_status_serializes_playback_retry_window() -> None:
    client, service = client_and_service()
    service.status_override = _status(
        volume=70,
        output_error_code="backend_process_failed",
        playback_supervisor_state="retry_scheduled",
        next_retry_at=datetime(2026, 6, 9, 12, 0, 5, tzinfo=timezone.utc),
        retry_attempts=0,
        max_retry_attempts=1,
    )

    response = client.get("/status")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"]["playback_supervisor"]["state"] == "retry_scheduled"
    assert payload["status"]["playback_supervisor"]["next_retry_at"] == (
        "2026-06-09T12:00:05+00:00"
    )
    assert payload["status"]["playback_supervisor"]["retry_attempts"] == 0
    assert payload["status"]["playback_supervisor"]["max_retry_attempts"] == 1


def test_status_includes_feed_monitor_when_configured() -> None:
    client, station, feeds = client_and_services()

    response = client.get("/status")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"]["station_state"] == "playing"
    assert payload["feed_monitor"]["running"] is False
    assert payload["feed_monitor"]["last_started_at"] == "2026-06-09T12:00:00+00:00"
    assert station.calls == [("status", None)]
    assert feeds.calls == ["status"]


def test_station_command_routes_call_station_service() -> None:
    client, service = client_and_service()

    expected_calls = [
        ("/play", ("play", None), "play"),
        ("/pause", ("pause", None), "pause"),
        ("/toggle", ("toggle", None), "toggle"),
        ("/stop", ("stop", None), "stop"),
        ("/next", ("next", None), "next"),
        ("/previous", ("previous", None), "previous"),
        ("/channel/next", ("next_channel", None), "channel.next"),
        ("/channel/previous", ("previous_channel", None), "channel.previous"),
        ("/podcast/next", ("next", None), "podcast.next"),
        ("/podcast/previous", ("previous", None), "podcast.previous"),
        ("/output/recover", ("recover_output", None), "output.recover"),
    ]

    for path, expected_call, expected_command in expected_calls:
        response = client.get(path)
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["ok"] is True
        assert payload["command"] == expected_command
        assert service.calls[-1] == expected_call


def test_output_recover_failure_returns_structured_503() -> None:
    client, service = client_and_service()
    service.errors["recover_output"] = CommandError(
        code="output_recovery_failed",
        message="Output recovery failed: ffmpeg missing",
    )

    response = client.get("/output/recover")

    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "error": {
            "code": "output_recovery_failed",
            "message": "Output recovery failed: ffmpeg missing",
        },
    }
    assert service.calls == [("recover_output", None)]


def test_channel_select_route_calls_station_service() -> None:
    client, service = client_and_service()

    response = client.get("/channel/stories")

    assert response.status_code == 200
    assert response.get_json()["command"] == "channel.select"
    assert service.calls == [("select_channel", "stories")]


def test_unknown_channel_returns_structured_404() -> None:
    client, service = client_and_service()
    service.errors["select_channel"] = CommandError(
        code="unknown_channel",
        message="Channel not found: bedtime",
    )

    response = client.get("/channel/bedtime")

    assert response.status_code == 404
    assert response.get_json() == {
        "ok": False,
        "error": {
            "code": "unknown_channel",
            "message": "Channel not found: bedtime",
        },
    }


def test_podcast_select_route_calls_station_service() -> None:
    client, service = client_and_service()

    response = client.get("/podcast/history-extra")

    assert response.status_code == 200
    assert response.get_json()["command"] == "podcast.select"
    assert service.calls == [("select_podcast", "history-extra")]


def test_unknown_podcast_returns_structured_404() -> None:
    client, service = client_and_service()
    service.errors["select_podcast"] = CommandError(
        code="unknown_podcast",
        message="Podcast not found in active channel: missing",
    )

    response = client.get("/podcast/missing")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "unknown_podcast"


def test_unavailable_podcast_returns_structured_409() -> None:
    client, service = client_and_service()
    service.errors["select_podcast"] = CommandError(
        code="podcast_unavailable",
        message="Podcast has no playable downloaded episode: science-hour",
    )

    response = client.get("/podcast/science-hour")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "podcast_unavailable"


def test_volume_returns_current_volume_without_command() -> None:
    client, service = client_and_service()

    response = client.get("/volume")

    assert response.status_code == 200
    assert response.get_json()["volume"] == 70
    assert service.calls == [("status", None)]


def test_volume_level_sets_integer_volume() -> None:
    client, service = client_and_service()

    response = client.get("/volume/42")

    assert response.status_code == 200
    assert response.get_json()["command"] == "volume.set"
    assert service.calls == [("set_volume", 42)]


def test_invalid_volume_text_returns_structured_400_without_service_command() -> None:
    client, service = client_and_service()

    response = client.get("/volume/loud")

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_volume"
    assert service.calls == []


def test_invalid_volume_range_returns_structured_400() -> None:
    client, service = client_and_service()
    service.errors["set_volume"] = CommandError(
        code="invalid_volume",
        message="Volume must be between 0 and 100.",
    )

    response = client.get("/volume/101")

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_volume"
    assert service.calls == [("set_volume", 101)]


def test_unknown_endpoint_returns_structured_404() -> None:
    client, service = client_and_service()

    response = client.get("/missing")

    assert response.status_code == 404
    assert response.get_json() == {
        "ok": False,
        "error": {
            "code": "not_found",
            "message": "Endpoint not found.",
        },
    }
    assert service.calls == []


def test_feeds_returns_feed_metadata_and_monitor_status() -> None:
    client, _station, feeds = client_and_services()

    response = client.get("/feeds")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["feed_monitor"]["running"] is False
    assert payload["feeds"][0]["podcast_id"] == "history-extra"
    assert payload["feeds"][0]["latest_episode"]["identity"] == "episode-1"
    assert feeds.calls == ["status", "feed_metadata"]


def test_feed_refresh_starts_background_refresh_quickly() -> None:
    client, _station, feeds = client_and_services()

    response = client.get("/feeds/refresh")

    payload = response.get_json()
    assert response.status_code == 202
    assert payload["ok"] is True
    assert payload["accepted"] is True
    assert payload["reason"] is None
    assert payload["feed_monitor"]["running"] is True
    assert feeds.calls == ["trigger_refresh", "status"]


def test_feed_refresh_reports_overlapping_refresh_without_error() -> None:
    client, _station, feeds = client_and_services()
    feeds.accept_refresh = False

    response = client.get("/feeds/refresh")

    assert response.status_code == 200
    assert response.get_json()["accepted"] is False
    assert response.get_json()["reason"] == "already_running"
    assert feeds.calls == ["trigger_refresh", "status"]


def test_volume_up_and_down_clamp_to_bounds() -> None:
    client, service = client_and_service()
    service.volume = 98

    up_response = client.get("/volume/up")
    service.volume = 3
    down_response = client.get("/volume/down")

    assert up_response.status_code == 200
    assert down_response.status_code == 200
    assert service.calls == [
        ("status", None),
        ("set_volume", 100),
        ("status", None),
        ("set_volume", 0),
    ]


def _status(
    *,
    volume: int,
    output_error_code: str | None = None,
    playback_supervisor_state: str | None = None,
    next_retry_at: datetime | None = None,
    retry_attempts: int = 0,
    max_retry_attempts: int = 0,
) -> StationStatus:
    podcast = PodcastConfig(
        id="history-extra",
        name="History Extra",
        feed_url="https://example.com/history-extra.xml",
    )
    return StationStatus(
        station_state="idle" if output_error_code is not None else "playing",
        active_channel=ChannelConfig(id="sleep", name="Sleep", podcasts=(podcast,)),
        active_podcast=podcast,
        active_episode=Episode(
            title="Latest episode",
            identity="episode-1",
            guid="guid-1",
            published_at=datetime(2026, 6, 8, 12, tzinfo=timezone.utc),
            media_url="https://example.com/history-extra.mp3",
            media_type="audio/mpeg",
            local_file=Path("/data/history-extra.mp3"),
            downloaded_at=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        ),
        volume=volume,
        output=OutputStatus(
            backend="fake",
            state="error" if output_error_code is not None else "playing",
            connected=output_error_code is None,
            current_episode_identity="episode-1",
            volume=volume,
            error=(
                OutputError(
                    code=output_error_code,
                    message="Output process exited unexpectedly with code 1.",
                )
                if output_error_code is not None
                else None
            ),
        ),
        playback_supervisor=PlaybackSupervisorStatus(
            state=(
                playback_supervisor_state
                or ("blocked" if output_error_code is not None else "watching")
            ),
            last_error=(
                OutputError(
                    code=output_error_code,
                    message="Output process exited unexpectedly with code 1.",
                )
                if output_error_code is not None
                else None
            ),
            next_retry_at=next_retry_at,
            retry_attempts=retry_attempts,
            max_retry_attempts=max_retry_attempts,
        ),
    )


def _json(response_payload: object) -> dict[str, Any]:
    if not isinstance(response_payload, dict):
        raise AssertionError("Expected JSON object")
    return response_payload
