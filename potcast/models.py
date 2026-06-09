"""Typed models used by Potcast."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path = Path("./data")
    episodes_dir: Path = Path("./data/episodes")


@dataclass(frozen=True)
class StationConfig:
    start_on_boot: bool = False
    shuffle_podcasts: bool = True
    shuffle_channels: bool = False
    volume: int = 100
    sleep_timer_minutes: int | None = None


@dataclass(frozen=True)
class IcecastOutputConfig:
    enabled: bool = True
    host: str = "icecast"
    port: int = 8000
    source_password: str | None = None
    mount: str = "/potcast.mp3"
    public_url: str | None = None
    name: str = "Potcast"
    description: str = "Personal podcast radio"
    genre: str = "Podcast"
    format: str = "mp3"
    bitrate_kbps: int = 128
    sample_rate_hz: int = 44100


@dataclass(frozen=True)
class LocalAudioOutputConfig:
    enabled: bool = False
    player: str = "mpv"
    device: str = "default"
    mixer: str = "software"


@dataclass(frozen=True)
class OutputsConfig:
    primary: str = "icecast"
    enabled: tuple[str, ...] = ("icecast",)
    icecast: IcecastOutputConfig = field(default_factory=IcecastOutputConfig)
    local_audio: LocalAudioOutputConfig = field(default_factory=LocalAudioOutputConfig)


@dataclass(frozen=True)
class FeedsConfig:
    refresh_interval_minutes: int = 30
    download_timeout_seconds: int = 60
    user_agent: str = "Potcast/0.1.0"


@dataclass(frozen=True)
class PodcastConfig:
    id: str
    name: str
    feed_url: str


@dataclass(frozen=True)
class Episode:
    title: str
    identity: str
    guid: str | None
    published_at: datetime | None
    media_url: str
    media_type: str
    duration: str | None = None
    local_file: Path | None = None
    downloaded_at: datetime | None = None


@dataclass(frozen=True)
class FeedParseError:
    code: str
    message: str


@dataclass(frozen=True)
class FeedParseResult:
    ok: bool
    latest_episode: Episode | None = None
    error: FeedParseError | None = None
    feed_title: str | None = None
    entry_count: int = 0
    playable_entry_count: int = 0


@dataclass(frozen=True)
class RuntimeState:
    station_status: str = "stopped"
    current_channel_id: str | None = None
    current_podcast_id: str | None = None
    volume: int = 100
    previous_podcast_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedMetadata:
    podcast_id: str
    feed_url: str
    status: str
    last_checked_at: datetime | None = None
    feed_title: str | None = None
    latest_episode: Episode | None = None
    entry_count: int = 0
    playable_entry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class DownloadMetadata:
    podcast_id: str
    episode_identity: str
    media_url: str
    media_type: str
    local_file: Path
    downloaded_at: datetime
    status: str = "downloaded"
    title: str | None = None


@dataclass(frozen=True)
class ChannelConfig:
    id: str
    name: str
    podcasts: tuple[PodcastConfig, ...]


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    storage: StorageConfig
    station: StationConfig
    outputs: OutputsConfig
    feeds: FeedsConfig
    channels: tuple[ChannelConfig, ...]
