"""YAML configuration loading and validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import yaml

from potcast.errors import ConfigError
from potcast.models import (
    AppConfig,
    ChannelConfig,
    FeedsConfig,
    IcecastOutputConfig,
    LocalAudioOutputConfig,
    OutputsConfig,
    PodcastConfig,
    ServerConfig,
    StationConfig,
    StorageConfig,
)

SUPPORTED_OUTPUTS = frozenset({"icecast", "local_audio"})


def load_config(path: str | Path) -> AppConfig:
    """Load and validate Potcast configuration from a YAML file."""

    config_path = Path(path)
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Config file must be UTF-8 text: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file: {config_path}") from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(_format_yaml_error(config_path, exc)) from exc

    return parse_config(raw)


def parse_config(raw: Any) -> AppConfig:
    """Parse and validate a raw YAML object."""

    root = _require_mapping(raw, "configuration")

    storage = _parse_storage(_optional_mapping(root, "storage"))
    outputs = _parse_outputs(_optional_mapping(root, "outputs"))

    return AppConfig(
        server=_parse_server(_optional_mapping(root, "server")),
        storage=storage,
        station=_parse_station(_optional_mapping(root, "station")),
        outputs=outputs,
        feeds=_parse_feeds(_optional_mapping(root, "feeds")),
        channels=_parse_channels(_required_sequence(root, "channels")),
    )


def _format_yaml_error(config_path: Path, exc: yaml.YAMLError) -> str:
    parts = [f"Could not parse YAML config: {config_path}"]

    problem = getattr(exc, "problem", None)
    if isinstance(problem, str) and problem:
        parts.append(problem)

    problem_mark = getattr(exc, "problem_mark", None)
    if problem_mark is not None:
        line = getattr(problem_mark, "line", None)
        column = getattr(problem_mark, "column", None)
        if isinstance(line, int) and isinstance(column, int):
            parts.append(f"line {line + 1}, column {column + 1}")

    if problem == "found character '\\t' that cannot start any token":
        parts.append("YAML indentation must use spaces, not tabs")

    return "; ".join(parts)


def _parse_server(raw: Mapping[str, Any]) -> ServerConfig:
    host = _string(raw, "host", default="0.0.0.0")
    port = _integer(raw, "port", default=8080)
    if not 1 <= port <= 65535:
        raise ConfigError("server.port must be between 1 and 65535")
    return ServerConfig(host=host, port=port)


def _parse_storage(raw: Mapping[str, Any]) -> StorageConfig:
    data_dir = Path(_string(raw, "data_dir", default="./data"))
    episodes_default = str(data_dir / "episodes")
    episodes_dir = Path(_string(raw, "episodes_dir", default=episodes_default))
    return StorageConfig(data_dir=data_dir, episodes_dir=episodes_dir)


def _parse_station(raw: Mapping[str, Any]) -> StationConfig:
    volume = _integer(raw, "volume", default=100)
    if not 0 <= volume <= 100:
        raise ConfigError("station.volume must be between 0 and 100")

    sleep_timer = _optional_integer(raw, "sleep_timer_minutes")
    if sleep_timer is not None and sleep_timer <= 0:
        raise ConfigError("station.sleep_timer_minutes must be greater than 0 when set")

    return StationConfig(
        start_on_boot=_boolean(raw, "start_on_boot", default=False),
        shuffle_podcasts=_boolean(raw, "shuffle_podcasts", default=True),
        shuffle_channels=_boolean(raw, "shuffle_channels", default=False),
        volume=volume,
        sleep_timer_minutes=sleep_timer,
    )


def _parse_outputs(raw: Mapping[str, Any]) -> OutputsConfig:
    primary = _string(raw, "primary", default="icecast")
    if primary not in SUPPORTED_OUTPUTS:
        raise ConfigError(f"outputs.primary must be one of: {', '.join(sorted(SUPPORTED_OUTPUTS))}")

    enabled = _string_tuple(raw, "enabled", default=(primary,))
    unknown = sorted(set(enabled) - SUPPORTED_OUTPUTS)
    if unknown:
        raise ConfigError(f"outputs.enabled contains unknown backend: {unknown[0]}")
    if primary not in enabled:
        raise ConfigError("outputs.primary must be included in outputs.enabled")

    icecast = _parse_icecast(_optional_mapping(raw, "icecast"))
    local_audio = _parse_local_audio(_optional_mapping(raw, "local_audio"))

    if "icecast" in enabled and not icecast.enabled:
        raise ConfigError("outputs.icecast.enabled must be true when icecast is enabled")
    if "local_audio" in enabled and not local_audio.enabled:
        raise ConfigError("outputs.local_audio.enabled must be true when local_audio is enabled")

    return OutputsConfig(
        primary=primary,
        enabled=enabled,
        icecast=icecast,
        local_audio=local_audio,
    )


def _parse_icecast(raw: Mapping[str, Any]) -> IcecastOutputConfig:
    port = _integer(raw, "port", default=8000)
    if not 1 <= port <= 65535:
        raise ConfigError("outputs.icecast.port must be between 1 and 65535")

    bitrate = _integer(raw, "bitrate_kbps", default=128)
    if bitrate <= 0:
        raise ConfigError("outputs.icecast.bitrate_kbps must be greater than 0")

    sample_rate = _integer(raw, "sample_rate_hz", default=44100)
    if sample_rate <= 0:
        raise ConfigError("outputs.icecast.sample_rate_hz must be greater than 0")

    mount = _string(raw, "mount", default="/potcast.mp3")
    if not mount.startswith("/"):
        raise ConfigError("outputs.icecast.mount must start with '/'")

    return IcecastOutputConfig(
        enabled=_boolean(raw, "enabled", default=True),
        host=_string(raw, "host", default="icecast"),
        port=port,
        source_password=_optional_string(raw, "source_password"),
        mount=mount,
        public_url=_optional_string(raw, "public_url"),
        name=_string(raw, "name", default="Potcast"),
        description=_string(raw, "description", default="Personal podcast radio"),
        genre=_string(raw, "genre", default="Podcast"),
        format=_string(raw, "format", default="mp3"),
        bitrate_kbps=bitrate,
        sample_rate_hz=sample_rate,
    )


def _parse_local_audio(raw: Mapping[str, Any]) -> LocalAudioOutputConfig:
    return LocalAudioOutputConfig(
        enabled=_boolean(raw, "enabled", default=False),
        player=_string(raw, "player", default="mpv"),
        device=_string(raw, "device", default="default"),
        mixer=_string(raw, "mixer", default="software"),
    )


def _parse_feeds(raw: Mapping[str, Any]) -> FeedsConfig:
    refresh_interval = _integer(raw, "refresh_interval_minutes", default=30)
    if refresh_interval <= 0:
        raise ConfigError("feeds.refresh_interval_minutes must be greater than 0")

    timeout = _integer(raw, "download_timeout_seconds", default=60)
    if timeout <= 0:
        raise ConfigError("feeds.download_timeout_seconds must be greater than 0")

    return FeedsConfig(
        refresh_interval_minutes=refresh_interval,
        download_timeout_seconds=timeout,
        user_agent=_string(raw, "user_agent", default="Potcast/0.1.0"),
    )


def _parse_channels(raw: Sequence[Any]) -> tuple[ChannelConfig, ...]:
    if len(raw) == 0:
        raise ConfigError("channels must contain at least one channel")

    channels: list[ChannelConfig] = []
    channel_ids: set[str] = set()
    podcast_ids: set[str] = set()

    for index, item in enumerate(raw):
        path = f"channels[{index}]"
        channel = _require_mapping(item, path)
        channel_id = _string(channel, "id", path=path)
        if channel_id in channel_ids:
            raise ConfigError(f"Duplicate channel id: {channel_id}")
        channel_ids.add(channel_id)

        podcasts_raw = _required_sequence(channel, "podcasts", path=path)
        if len(podcasts_raw) == 0:
            raise ConfigError(f"{path}.podcasts must contain at least one podcast")

        podcasts: list[PodcastConfig] = []
        for podcast_index, podcast_item in enumerate(podcasts_raw):
            podcast_path = f"{path}.podcasts[{podcast_index}]"
            podcast = _require_mapping(podcast_item, podcast_path)
            podcast_id = _string(podcast, "id", path=podcast_path)
            if podcast_id in podcast_ids:
                raise ConfigError(f"Duplicate podcast id: {podcast_id}")
            podcast_ids.add(podcast_id)

            podcasts.append(
                PodcastConfig(
                    id=podcast_id,
                    name=_string(podcast, "name", path=podcast_path),
                    feed_url=_string(podcast, "feed_url", path=podcast_path),
                )
            )

        channels.append(
            ChannelConfig(
                id=channel_id,
                name=_string(channel, "name", path=path),
                podcasts=tuple(podcasts),
            )
        )

    return tuple(channels)


def _optional_mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key, {})
    if value is None:
        return {}
    return _require_mapping(value, key)


def _require_mapping(raw: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return raw


def _required_sequence(raw: Mapping[str, Any], key: str, path: str | None = None) -> Sequence[Any]:
    field_path = _field_path(key, path)
    if key not in raw:
        raise ConfigError(f"Missing required field: {field_path}")
    value = raw[key]
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ConfigError(f"{field_path} must be a list")
    return cast(Sequence[Any], value)


def _string(
    raw: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
    path: str | None = None,
) -> str:
    field_path = _field_path(key, path)
    if key not in raw:
        if default is not None:
            return default
        raise ConfigError(f"Missing required field: {field_path}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_path} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when set")
    return value


def _integer(raw: Mapping[str, Any], key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return cast(int, value)


def _optional_integer(raw: Mapping[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer when set")
    return cast(int, value)


def _boolean(raw: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _string_tuple(raw: Mapping[str, Any], key: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    value = raw.get(key, default)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ConfigError(f"{key} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{key}[{index}] must be a non-empty string")
        result.append(item)
    if not result:
        raise ConfigError(f"{key} must contain at least one backend")
    return tuple(result)


def _field_path(key: str, path: str | None) -> str:
    if path is None:
        return key
    return f"{path}.{key}"
