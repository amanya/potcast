"""Runtime composition for starting Potcast from a config file."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from flask import Flask

from potcast.app import AppServices, create_app
from potcast.config import load_config
from potcast.downloader import AtomicEpisodeDownloader
from potcast.feed_refresh import FeedRefreshService, HttpxDownloadWriter, HttpxFeedFetcher
from potcast.models import (
    AppConfig,
    FeedMetadata,
    FeedMonitorStatus,
    FeedRefreshTriggerResult,
    RuntimeState,
)
from potcast.outputs.base import OutputBackend, ProcessLauncher
from potcast.outputs.icecast import IcecastOutputBackend
from potcast.outputs.local_audio import LocalAudioOutputBackend
from potcast.scheduler import PeriodicScheduler
from potcast.service import StationService
from potcast.state import JsonStateStore, ensure_data_directories

LOGGER = logging.getLogger(__name__)


class AppRunner(Protocol):
    """Callable used to run the composed Flask app."""

    def __call__(self, app: Flask, *, host: str, port: int) -> None: ...


@dataclass(frozen=True)
class RuntimeServices:
    """Long-lived services created by runtime composition."""

    state_store: JsonStateStore
    station: StationService
    feeds: ScheduledFeedMonitor
    scheduler: PeriodicScheduler


@dataclass(frozen=True)
class PotcastRuntime:
    """Composed Potcast runtime and lifecycle hooks."""

    config: AppConfig
    app: Flask
    services: RuntimeServices

    def start(self) -> None:
        LOGGER.info("Starting feed scheduler")
        self.services.scheduler.start()
        if self.config.station.start_on_boot:
            LOGGER.info("Starting station because station.start_on_boot is enabled")
            self.services.station.play()

    def stop(self) -> None:
        LOGGER.info("Stopping Potcast runtime")
        self.services.scheduler.stop(timeout=5)
        self.services.station.stop()

    def run(self, runner: AppRunner | None = None) -> None:
        app_runner = runner or run_flask_app
        self.start()
        try:
            app_runner(self.app, host=self.config.server.host, port=self.config.server.port)
        finally:
            self.stop()


class ScheduledFeedMonitor:
    """Expose feed refresh status enriched with scheduler timing."""

    def __init__(
        self,
        refresh_service: FeedRefreshService,
        scheduler: PeriodicScheduler,
    ) -> None:
        self.refresh_service = refresh_service
        self.scheduler = scheduler

    def trigger_refresh(self) -> FeedRefreshTriggerResult:
        return self.refresh_service.trigger_refresh()

    def status(self) -> FeedMonitorStatus:
        return replace(
            self.refresh_service.status(),
            next_refresh_at=self.scheduler.status().next_run_at,
        )

    def feed_metadata(self) -> dict[str, FeedMetadata]:
        return self.refresh_service.feed_metadata()


def build_runtime(
    config_path: str | Path,
    *,
    process_launcher: ProcessLauncher | None = None,
    background_starter: Callable[[Callable[[], None]], None] | None = None,
) -> PotcastRuntime:
    """Load config and compose state, services, scheduler, output, and HTTP app."""

    config = load_config(config_path)
    ensure_data_directories(config.storage)

    state_store = JsonStateStore(config.storage.data_dir)
    _initialize_runtime_state(state_store, config)
    output = build_output_backend(config, process_launcher=process_launcher)
    station = StationService(config.channels, config.station, state_store, output)

    fetcher = HttpxFeedFetcher(
        timeout_seconds=config.feeds.download_timeout_seconds,
        user_agent=config.feeds.user_agent,
    )
    downloader = AtomicEpisodeDownloader(
        config.storage.episodes_dir,
        HttpxDownloadWriter(
            timeout_seconds=config.feeds.download_timeout_seconds,
            user_agent=config.feeds.user_agent,
        ),
    )
    refresh_service = FeedRefreshService(
        config.channels,
        state_store,
        fetcher,
        downloader,
        background_starter=background_starter,
    )
    scheduler = PeriodicScheduler(
        interval=timedelta(minutes=config.feeds.refresh_interval_minutes),
        job=refresh_service.trigger_refresh,
    )
    feeds = ScheduledFeedMonitor(refresh_service, scheduler)
    services = RuntimeServices(
        state_store=state_store,
        station=station,
        feeds=feeds,
        scheduler=scheduler,
    )
    app = create_app(config=config, services=AppServices(station=station, feeds=feeds))

    LOGGER.info("Potcast runtime composed from %s", config_path)
    return PotcastRuntime(config=config, app=app, services=services)


def build_output_backend(
    config: AppConfig,
    *,
    process_launcher: ProcessLauncher | None = None,
) -> OutputBackend:
    """Create the configured primary output backend."""

    if config.outputs.primary == "icecast":
        return IcecastOutputBackend(
            config.outputs.icecast,
            launcher=process_launcher,
            volume=config.station.volume,
        )
    if config.outputs.primary == "local_audio":
        return LocalAudioOutputBackend(
            config.outputs.local_audio,
            launcher=process_launcher,
            volume=config.station.volume,
        )
    raise ValueError(f"Unsupported output backend: {config.outputs.primary}")


def configure_logging(level: str = "INFO") -> None:
    """Configure basic process logging."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def run_flask_app(app: Flask, *, host: str, port: int) -> None:
    """Run the HTTP server used by the packaged CLI."""

    app.run(host=host, port=port)


def _initialize_runtime_state(state_store: JsonStateStore, config: AppConfig) -> None:
    state_path = config.storage.data_dir / "state.json"
    if state_path.exists():
        return
    state_store.save_runtime_state(RuntimeState(volume=config.station.volume))
