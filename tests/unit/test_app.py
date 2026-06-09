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
    OutputStatus,
    PodcastConfig,
    StationCommandResult,
    StationStatus,
)


class SpyStationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.volume = 70
        self.errors: dict[str, CommandError] = {}

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

    def status(self) -> StationStatus:
        self.calls.append(("status", None))
        return _status(volume=self.volume)

    def _command(self, name: str, arg: object | None = None) -> StationCommandResult:
        self.calls.append((name, arg))
        error = self.errors.get(name)
        return StationCommandResult(
            ok=error is None,
            status=_status(volume=self.volume),
            error=error,
        )


def client_and_service() -> tuple[FlaskClient, SpyStationService]:
    service = SpyStationService()
    app = create_app(services=AppServices(station=service))
    return app.test_client(), service


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
    ]

    for path, expected_call, expected_command in expected_calls:
        response = client.get(path)
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["ok"] is True
        assert payload["command"] == expected_command
        assert service.calls[-1] == expected_call


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


def _status(*, volume: int) -> StationStatus:
    podcast = PodcastConfig(
        id="history-extra",
        name="History Extra",
        feed_url="https://example.com/history-extra.xml",
    )
    return StationStatus(
        station_state="playing",
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
            state="playing",
            connected=True,
            current_episode_identity="episode-1",
            volume=volume,
        ),
    )


def _json(response_payload: object) -> dict[str, Any]:
    if not isinstance(response_payload, dict):
        raise AssertionError("Expected JSON object")
    return response_payload
