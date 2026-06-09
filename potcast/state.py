"""Filesystem-backed runtime metadata persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from potcast.errors import StorageError
from potcast.models import DownloadMetadata, Episode, FeedMetadata, RuntimeState, StorageConfig


class JsonStateStore:
    """Persist runtime state and metadata as JSON files under the data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def load_runtime_state(self) -> RuntimeState:
        path = self.data_dir / "state.json"
        if not path.exists():
            return RuntimeState()
        return _runtime_state_from_json(_read_json_object(path))

    def save_runtime_state(self, state: RuntimeState) -> None:
        _write_json_atomic(self.data_dir / "state.json", _runtime_state_to_json(state))

    def load_feed_metadata(self) -> dict[str, FeedMetadata]:
        path = self.data_dir / "feeds.json"
        if not path.exists():
            return {}
        raw = _read_json_object(path)
        return {
            podcast_id: _feed_metadata_from_json(_object_value(value, podcast_id))
            for podcast_id, value in raw.items()
        }

    def save_feed_metadata(self, metadata: Mapping[str, FeedMetadata]) -> None:
        payload = {
            podcast_id: _feed_metadata_to_json(feed)
            for podcast_id, feed in sorted(metadata.items())
        }
        _write_json_atomic(self.data_dir / "feeds.json", payload)

    def load_download_metadata(self) -> dict[str, DownloadMetadata]:
        path = self.data_dir / "downloads.json"
        if not path.exists():
            return {}
        raw = _read_json_object(path)
        return {
            podcast_id: _download_metadata_from_json(_object_value(value, podcast_id))
            for podcast_id, value in raw.items()
        }

    def save_download_metadata(self, metadata: Mapping[str, DownloadMetadata]) -> None:
        payload = {
            podcast_id: _download_metadata_to_json(download)
            for podcast_id, download in sorted(metadata.items())
        }
        _write_json_atomic(self.data_dir / "downloads.json", payload)


def ensure_data_directories(storage: StorageConfig) -> None:
    """Create Potcast's runtime metadata and episode directories."""

    try:
        storage.data_dir.mkdir(parents=True, exist_ok=True)
        storage.episodes_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError("Could not create Potcast data directories.") from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except OSError as exc:
        raise StorageError(f"Could not read runtime metadata file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageError(f"Could not parse runtime metadata file: {path}") from exc

    if not isinstance(payload, dict):
        raise StorageError(f"Runtime metadata file must contain a JSON object: {path}")
    return cast(dict[str, Any], payload)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            json.dump(payload, file, indent=2, sort_keys=True)
            file.write("\n")
        os.replace(temp_path, path)
    except OSError as exc:
        raise StorageError(f"Could not write runtime metadata file: {path}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _runtime_state_to_json(state: RuntimeState) -> dict[str, Any]:
    return {
        "station_status": state.station_status,
        "current_channel_id": state.current_channel_id,
        "current_podcast_id": state.current_podcast_id,
        "volume": state.volume,
        "previous_podcast_ids": list(state.previous_podcast_ids),
    }


def _runtime_state_from_json(raw: Mapping[str, Any]) -> RuntimeState:
    return RuntimeState(
        station_status=_string(raw, "station_status", default="stopped"),
        current_channel_id=_optional_string(raw, "current_channel_id"),
        current_podcast_id=_optional_string(raw, "current_podcast_id"),
        volume=_integer(raw, "volume", default=100),
        previous_podcast_ids=_string_tuple(raw, "previous_podcast_ids"),
    )


def _feed_metadata_to_json(metadata: FeedMetadata) -> dict[str, Any]:
    return {
        "podcast_id": metadata.podcast_id,
        "feed_url": metadata.feed_url,
        "status": metadata.status,
        "last_checked_at": _datetime_to_json(metadata.last_checked_at),
        "feed_title": metadata.feed_title,
        "latest_episode": _episode_to_json(metadata.latest_episode),
        "entry_count": metadata.entry_count,
        "playable_entry_count": metadata.playable_entry_count,
        "error_code": metadata.error_code,
        "error_message": metadata.error_message,
    }


def _feed_metadata_from_json(raw: Mapping[str, Any]) -> FeedMetadata:
    return FeedMetadata(
        podcast_id=_string(raw, "podcast_id"),
        feed_url=_string(raw, "feed_url"),
        status=_string(raw, "status"),
        last_checked_at=_optional_datetime(raw, "last_checked_at"),
        feed_title=_optional_string(raw, "feed_title"),
        latest_episode=_optional_episode(raw, "latest_episode"),
        entry_count=_integer(raw, "entry_count", default=0),
        playable_entry_count=_integer(raw, "playable_entry_count", default=0),
        error_code=_optional_string(raw, "error_code"),
        error_message=_optional_string(raw, "error_message"),
    )


def _download_metadata_to_json(metadata: DownloadMetadata) -> dict[str, Any]:
    return {
        "podcast_id": metadata.podcast_id,
        "episode_identity": metadata.episode_identity,
        "media_url": metadata.media_url,
        "media_type": metadata.media_type,
        "local_file": str(metadata.local_file),
        "downloaded_at": _datetime_to_json(metadata.downloaded_at),
        "status": metadata.status,
        "title": metadata.title,
    }


def _download_metadata_from_json(raw: Mapping[str, Any]) -> DownloadMetadata:
    downloaded_at = _optional_datetime(raw, "downloaded_at")
    if downloaded_at is None:
        raise StorageError("downloaded_at must be set for download metadata")

    return DownloadMetadata(
        podcast_id=_string(raw, "podcast_id"),
        episode_identity=_string(raw, "episode_identity"),
        media_url=_string(raw, "media_url"),
        media_type=_string(raw, "media_type"),
        local_file=Path(_string(raw, "local_file")),
        downloaded_at=downloaded_at,
        status=_string(raw, "status", default="downloaded"),
        title=_optional_string(raw, "title"),
    )


def _episode_to_json(episode: Episode | None) -> dict[str, Any] | None:
    if episode is None:
        return None
    return {
        "title": episode.title,
        "identity": episode.identity,
        "guid": episode.guid,
        "published_at": _datetime_to_json(episode.published_at),
        "media_url": episode.media_url,
        "media_type": episode.media_type,
        "duration": episode.duration,
        "local_file": str(episode.local_file) if episode.local_file is not None else None,
        "downloaded_at": _datetime_to_json(episode.downloaded_at),
    }


def _episode_from_json(raw: Mapping[str, Any]) -> Episode:
    local_file = _optional_string(raw, "local_file")
    return Episode(
        title=_string(raw, "title"),
        identity=_string(raw, "identity"),
        guid=_optional_string(raw, "guid"),
        published_at=_optional_datetime(raw, "published_at"),
        media_url=_string(raw, "media_url"),
        media_type=_string(raw, "media_type"),
        duration=_optional_string(raw, "duration"),
        local_file=Path(local_file) if local_file is not None else None,
        downloaded_at=_optional_datetime(raw, "downloaded_at"),
    )


def _datetime_to_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _optional_datetime(raw: Mapping[str, Any], key: str) -> datetime | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageError(f"{key} must be an ISO datetime string when set")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise StorageError(f"{key} must be an ISO datetime string when set") from exc


def _optional_episode(raw: Mapping[str, Any], key: str) -> Episode | None:
    value = raw.get(key)
    if value is None:
        return None
    return _episode_from_json(_object_value(value, key))


def _object_value(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StorageError(f"{key} must be an object")
    return cast(Mapping[str, Any], value)


def _string(raw: Mapping[str, Any], key: str, *, default: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value:
        raise StorageError(f"{key} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise StorageError(f"{key} must be a non-empty string when set")
    return value


def _integer(raw: Mapping[str, Any], key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StorageError(f"{key} must be an integer")
    return cast(int, value)


def _string_tuple(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key, ())
    if not isinstance(value, list):
        raise StorageError(f"{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise StorageError(f"{key} must be a list of strings")
        result.append(item)
    return tuple(result)
