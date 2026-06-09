"""HTTP app factory for Potcast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from flask import Flask, jsonify
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import NotFound

from potcast import __version__
from potcast.models import (
    AppConfig,
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

VOLUME_STEP = 5


class StationController(Protocol):
    """Station service methods used by the HTTP delivery layer."""

    def play(self) -> StationCommandResult: ...

    def pause(self) -> StationCommandResult: ...

    def toggle(self) -> StationCommandResult: ...

    def stop(self) -> StationCommandResult: ...

    def next(self) -> StationCommandResult: ...

    def previous(self) -> StationCommandResult: ...

    def next_channel(self) -> StationCommandResult: ...

    def previous_channel(self) -> StationCommandResult: ...

    def select_channel(self, channel_id: str) -> StationCommandResult: ...

    def select_podcast(self, podcast_id: str) -> StationCommandResult: ...

    def set_volume(self, volume: int) -> StationCommandResult: ...

    def recover_output(self) -> StationCommandResult: ...

    def status(self) -> StationStatus: ...


class FeedMonitorController(Protocol):
    """Feed monitor methods used by the HTTP delivery layer."""

    def trigger_refresh(self) -> FeedRefreshTriggerResult: ...

    def status(self) -> FeedMonitorStatus: ...

    def feed_metadata(self) -> dict[str, FeedMetadata]: ...


@dataclass(frozen=True)
class AppServices:
    """Services injected into the Flask app factory."""

    station: StationController
    feeds: FeedMonitorController | None = None


def create_app(config: AppConfig | None = None, services: AppServices | None = None) -> Flask:
    app = Flask(__name__)
    app.config["POTCAST_CONFIG"] = config
    app.config["POTCAST_SERVICES"] = services

    @app.get("/health")
    def health() -> ResponseReturnValue:
        return jsonify({"ok": True, "version": __version__})

    @app.get("/status")
    def status() -> ResponseReturnValue:
        payload: dict[str, Any] = {"ok": True, "status": _status_to_json(_station(app).status())}
        feeds = _feeds(app)
        if feeds is not None:
            payload["feed_monitor"] = _feed_monitor_to_json(feeds.status())
        return jsonify(payload)

    @app.get("/play")
    def play() -> ResponseReturnValue:
        return _command_response("play", _station(app).play())

    @app.get("/pause")
    def pause() -> ResponseReturnValue:
        return _command_response("pause", _station(app).pause())

    @app.get("/toggle")
    def toggle() -> ResponseReturnValue:
        return _command_response("toggle", _station(app).toggle())

    @app.get("/stop")
    def stop() -> ResponseReturnValue:
        return _command_response("stop", _station(app).stop())

    @app.get("/next")
    def next_podcast() -> ResponseReturnValue:
        return _command_response("next", _station(app).next())

    @app.get("/previous")
    def previous_podcast() -> ResponseReturnValue:
        return _command_response("previous", _station(app).previous())

    @app.get("/channel/next")
    def next_channel() -> ResponseReturnValue:
        return _command_response("channel.next", _station(app).next_channel())

    @app.get("/channel/previous")
    def previous_channel() -> ResponseReturnValue:
        return _command_response("channel.previous", _station(app).previous_channel())

    @app.get("/channel/<channel_id>")
    def select_channel(channel_id: str) -> ResponseReturnValue:
        return _command_response("channel.select", _station(app).select_channel(channel_id))

    @app.get("/podcast/next")
    def podcast_next() -> ResponseReturnValue:
        return _command_response("podcast.next", _station(app).next())

    @app.get("/podcast/previous")
    def podcast_previous() -> ResponseReturnValue:
        return _command_response("podcast.previous", _station(app).previous())

    @app.get("/podcast/<podcast_id>")
    def select_podcast(podcast_id: str) -> ResponseReturnValue:
        return _command_response("podcast.select", _station(app).select_podcast(podcast_id))

    @app.get("/output/recover")
    def recover_output() -> ResponseReturnValue:
        return _command_response("output.recover", _station(app).recover_output())

    @app.get("/volume")
    def volume() -> ResponseReturnValue:
        status = _station(app).status()
        return jsonify({"ok": True, "volume": status.volume, "status": _status_to_json(status)})

    @app.get("/volume/up")
    def volume_up() -> ResponseReturnValue:
        station = _station(app)
        level = min(100, station.status().volume + VOLUME_STEP)
        return _command_response("volume.up", station.set_volume(level))

    @app.get("/volume/down")
    def volume_down() -> ResponseReturnValue:
        station = _station(app)
        level = max(0, station.status().volume - VOLUME_STEP)
        return _command_response("volume.down", station.set_volume(level))

    @app.get("/volume/<level>")
    def set_volume(level: str) -> ResponseReturnValue:
        try:
            volume_level = int(level)
        except ValueError:
            return _error_response(
                CommandError(
                    code="invalid_volume",
                    message="Volume must be an integer between 0 and 100.",
                )
            )
        return _command_response("volume.set", _station(app).set_volume(volume_level))

    @app.get("/feeds")
    def feeds() -> ResponseReturnValue:
        monitor = _require_feeds(app)
        return jsonify(
            {
                "ok": True,
                "feed_monitor": _feed_monitor_to_json(monitor.status()),
                "feeds": [
                    _feed_metadata_to_json(metadata)
                    for metadata in sorted(
                        monitor.feed_metadata().values(),
                        key=lambda item: item.podcast_id,
                    )
                ],
            }
        )

    @app.get("/feeds/refresh")
    def refresh_feeds() -> ResponseReturnValue:
        result = _require_feeds(app).trigger_refresh()
        return (
            jsonify(
                {
                    "ok": True,
                    "accepted": result.accepted,
                    "reason": result.reason,
                    "feed_monitor": _feed_monitor_to_json(result.status),
                }
            ),
            202 if result.accepted else 200,
        )

    @app.errorhandler(404)
    def not_found(_error: NotFound) -> ResponseReturnValue:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "not_found",
                        "message": "Endpoint not found.",
                    },
                }
            ),
            404,
        )

    return app


def _station(app: Flask) -> StationController:
    services = app.config.get("POTCAST_SERVICES")
    if not isinstance(services, AppServices):
        raise RuntimeError("Potcast app requires AppServices.")
    return services.station


def _feeds(app: Flask) -> FeedMonitorController | None:
    services = app.config.get("POTCAST_SERVICES")
    if not isinstance(services, AppServices):
        raise RuntimeError("Potcast app requires AppServices.")
    return services.feeds


def _require_feeds(app: Flask) -> FeedMonitorController:
    feeds = _feeds(app)
    if feeds is None:
        raise RuntimeError("Potcast app requires a feed monitor service for feed endpoints.")
    return feeds


def _status_response(status: StationStatus) -> ResponseReturnValue:
    return jsonify({"ok": True, "status": _status_to_json(status)})


def _command_response(command: str, result: StationCommandResult) -> ResponseReturnValue:
    if not result.ok:
        return _error_response(result.error)
    return jsonify(
        {
            "ok": True,
            "command": command,
            "status": _status_to_json(result.status),
        }
    )


def _error_response(error: CommandError | None) -> ResponseReturnValue:
    if error is None:
        error = CommandError(
            code="command_failed",
            message="Command failed without a structured error.",
        )
    return jsonify({"ok": False, "error": _command_error_to_json(error)}), _status_code(error)


def _status_code(error: CommandError) -> int:
    if error.code in {"unknown_channel", "unknown_podcast"}:
        return 404
    if error.code == "invalid_volume":
        return 400
    if error.code == "podcast_unavailable":
        return 409
    return 500


def _status_to_json(status: StationStatus) -> dict[str, Any]:
    return {
        "station_state": status.station_state,
        "active_channel": _channel_to_json(status.active_channel),
        "active_podcast": _podcast_to_json(status.active_podcast),
        "active_episode": _episode_to_json(status.active_episode),
        "volume": status.volume,
        "output": _output_status_to_json(status.output),
        "playback_supervisor": _playback_supervisor_to_json(status.playback_supervisor),
    }


def _channel_to_json(channel: ChannelConfig | None) -> dict[str, Any] | None:
    if channel is None:
        return None
    return {
        "id": channel.id,
        "name": channel.name,
        "podcasts": [_podcast_to_json(podcast) for podcast in channel.podcasts],
    }


def _podcast_to_json(podcast: PodcastConfig | None) -> dict[str, str] | None:
    if podcast is None:
        return None
    return {
        "id": podcast.id,
        "name": podcast.name,
        "feed_url": podcast.feed_url,
    }


def _episode_to_json(episode: Episode | None) -> dict[str, Any] | None:
    if episode is None:
        return None
    return {
        "title": episode.title,
        "identity": episode.identity,
        "guid": episode.guid,
        "published_at": _value_to_json(episode.published_at),
        "media_url": episode.media_url,
        "media_type": episode.media_type,
        "duration": episode.duration,
        "local_file": _value_to_json(episode.local_file),
        "downloaded_at": _value_to_json(episode.downloaded_at),
    }


def _feed_metadata_to_json(metadata: FeedMetadata) -> dict[str, Any]:
    return {
        "podcast_id": metadata.podcast_id,
        "feed_url": metadata.feed_url,
        "status": metadata.status,
        "last_checked_at": _value_to_json(metadata.last_checked_at),
        "feed_title": metadata.feed_title,
        "latest_episode": _episode_to_json(metadata.latest_episode),
        "entry_count": metadata.entry_count,
        "playable_entry_count": metadata.playable_entry_count,
        "error": (
            {
                "code": metadata.error_code,
                "message": metadata.error_message,
            }
            if metadata.error_code is not None
            else None
        ),
    }


def _feed_monitor_to_json(status: FeedMonitorStatus) -> dict[str, Any]:
    return {
        "running": status.running,
        "last_started_at": _value_to_json(status.last_started_at),
        "last_finished_at": _value_to_json(status.last_finished_at),
        "last_result": status.last_result,
        "last_error": (
            {
                "code": status.last_error_code,
                "message": status.last_error_message,
            }
            if status.last_error_code is not None
            else None
        ),
        "next_refresh_at": _value_to_json(status.next_refresh_at),
    }


def _output_status_to_json(status: OutputStatus) -> dict[str, Any]:
    return {
        "backend": status.backend,
        "state": status.state,
        "connected": status.connected,
        "current_episode_identity": status.current_episode_identity,
        "volume": status.volume,
        "error": _output_error_to_json(status.error),
    }


def _output_error_to_json(error: OutputError | None) -> dict[str, str] | None:
    if error is None:
        return None
    return {
        "code": error.code,
        "message": error.message,
    }


def _playback_supervisor_to_json(status: PlaybackSupervisorStatus) -> dict[str, Any]:
    return {
        "state": status.state,
        "last_error": _output_error_to_json(status.last_error),
    }


def _command_error_to_json(error: CommandError) -> dict[str, str]:
    return {
        "code": error.code,
        "message": error.message,
    }


def _value_to_json(value: datetime | Path | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)
