"""Application service for station commands and output coordination."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from potcast.models import (
    ChannelConfig,
    CommandError,
    DownloadMetadata,
    Episode,
    PodcastConfig,
    RuntimeState,
    StationCommandResult,
    StationConfig,
    StationStatus,
)
from potcast.outputs.base import OutputBackend
from potcast.station import Randomizer, StationSelector


class StationStateStore(Protocol):
    """Persistence required by the station service."""

    def load_runtime_state(self) -> RuntimeState: ...

    def save_runtime_state(self, state: RuntimeState) -> None: ...

    def load_download_metadata(self) -> dict[str, DownloadMetadata]: ...


class StationService:
    """Coordinate runtime station state, selection, downloads, and output."""

    def __init__(
        self,
        channels: Sequence[ChannelConfig],
        station: StationConfig,
        state_store: StationStateStore,
        output: OutputBackend,
        *,
        randomizer: Randomizer | None = None,
    ) -> None:
        self.channels = tuple(channels)
        self.station = station
        self.state_store = state_store
        self.output = output
        self.randomizer = randomizer

    def play(self) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._activate(state, downloads)
        if self._is_playing_selected(state, downloads):
            self._save_state(state)
            return self._result(state, downloads)

        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def pause(self) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._activate(state, downloads)
        if state.station_status != "paused":
            self.output.pause()
            state = replace(state, station_status="paused")
        self._save_state(state)
        return self._result(state, downloads)

    def toggle(self) -> StationCommandResult:
        if self._load_state().station_status == "playing":
            return self.pause()
        return self.play()

    def stop(self) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        if state.station_status != "stopped":
            self.output.stop()
            state = replace(state, station_status="stopped")
        self._save_state(state)
        return self._result(state, downloads)

    def next(self) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._selector(downloads).next_podcast(state)
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def previous(self) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._selector(downloads).previous_podcast(state)
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def next_channel(self) -> StationCommandResult:
        return self._change_channel(offset=1)

    def previous_channel(self) -> StationCommandResult:
        return self._change_channel(offset=-1)

    def select_channel(self, channel_id: str) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        channel = self._channel_by_id(channel_id)
        if channel is None:
            return self._result(
                state,
                downloads,
                error=CommandError(
                    code="unknown_channel",
                    message=f"Channel not found: {channel_id}",
                ),
            )

        podcast = self._first_playable_podcast(channel, downloads)
        state = replace(
            state,
            current_channel_id=channel.id,
            current_podcast_id=podcast.id if podcast is not None else None,
            previous_podcast_ids=(),
        )
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def select_podcast(self, podcast_id: str) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._activate(state, downloads)
        channel = self._active_channel(state)
        podcast = self._podcast_in_channel(channel, podcast_id)
        if podcast is None:
            return self._result(
                state,
                downloads,
                error=CommandError(
                    code="unknown_podcast",
                    message=f"Podcast not found in active channel: {podcast_id}",
                ),
            )
        if podcast_id not in self._playable_podcast_ids(downloads):
            return self._result(
                state,
                downloads,
                error=CommandError(
                    code="podcast_unavailable",
                    message=f"Podcast has no playable downloaded episode: {podcast_id}",
                ),
            )

        state = replace(
            state,
            current_channel_id=channel.id if channel is not None else None,
            current_podcast_id=podcast.id,
            previous_podcast_ids=(),
        )
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def set_volume(self, volume: int) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        if volume < 0 or volume > 100:
            return self._result(
                state,
                downloads,
                error=CommandError(
                    code="invalid_volume",
                    message="Volume must be between 0 and 100.",
                ),
            )

        state = replace(state, volume=volume)
        self.output.set_volume(volume)
        self._save_state(state)
        return self._result(state, downloads)

    def status(self) -> StationStatus:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        state = self._activate(state, downloads)
        return self._status(state, downloads)

    def recover_output(self) -> StationCommandResult:
        """Clear an output error and retry the selected episode once."""

        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        if self.output.status().state != "error":
            return self._result(state, downloads)

        self.output.stop()
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def advance_if_finished(self) -> StationCommandResult:
        """Advance once when the active output reports normal episode completion."""

        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        if state.station_status != "playing":
            return self._result(state, downloads)
        event = self.output.consume_playback_event()
        if event is None:
            return self._result(state, downloads)
        if event.outcome != "completed":
            state = replace(state, station_status="idle")
            self._save_state(state)
            return self._result(state, downloads)

        state = self._selector(downloads).next_podcast(state)
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def _change_channel(self, *, offset: int) -> StationCommandResult:
        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        selector = self._selector(downloads)
        state = selector.next_channel(state) if offset > 0 else selector.previous_channel(state)
        state = self._play_selected(state, downloads)
        self._save_state(state)
        return self._result(state, downloads)

    def _load_state(self) -> RuntimeState:
        return self.state_store.load_runtime_state()

    def _save_state(self, state: RuntimeState) -> None:
        self.state_store.save_runtime_state(state)

    def _activate(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> RuntimeState:
        return self._selector(downloads).activate(state)

    def _selector(self, downloads: Mapping[str, DownloadMetadata]) -> StationSelector:
        return StationSelector(
            self.channels,
            self.station,
            playable_podcast_ids=self._playable_podcast_ids(downloads),
            randomizer=self.randomizer,
        )

    def _play_selected(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> RuntimeState:
        state = self._activate(state, downloads)
        episode = self._active_episode(state, downloads)
        if episode is None:
            self.output.stop()
            return replace(state, station_status="idle")

        self.output.play_episode(episode)
        if self.output.status().state == "error":
            return replace(state, station_status="idle")
        return replace(state, station_status="playing")

    def _is_playing_selected(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> bool:
        if state.station_status != "playing":
            return False
        episode = self._active_episode(state, downloads)
        if episode is None:
            return False
        return self.output.status().current_episode_identity == episode.identity

    def _result(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
        *,
        error: CommandError | None = None,
    ) -> StationCommandResult:
        return StationCommandResult(
            ok=error is None,
            status=self._status(state, downloads),
            error=error,
        )

    def _status(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> StationStatus:
        channel = self._active_channel(state)
        podcast = self._podcast_in_channel(channel, state.current_podcast_id)
        return StationStatus(
            station_state=state.station_status,
            active_channel=channel,
            active_podcast=podcast,
            active_episode=self._active_episode(state, downloads),
            volume=state.volume,
            output=self.output.status(),
        )

    def _active_channel(self, state: RuntimeState) -> ChannelConfig | None:
        if state.current_channel_id is not None:
            return self._channel_by_id(state.current_channel_id)
        return self.channels[0] if self.channels else None

    def _channel_by_id(self, channel_id: str) -> ChannelConfig | None:
        for channel in self.channels:
            if channel.id == channel_id:
                return channel
        return None

    def _podcast_in_channel(
        self,
        channel: ChannelConfig | None,
        podcast_id: str | None,
    ) -> PodcastConfig | None:
        if channel is None or podcast_id is None:
            return None
        for podcast in channel.podcasts:
            if podcast.id == podcast_id:
                return podcast
        return None

    def _first_playable_podcast(
        self,
        channel: ChannelConfig,
        downloads: Mapping[str, DownloadMetadata],
    ) -> PodcastConfig | None:
        playable_ids = self._playable_podcast_ids(downloads)
        for podcast in channel.podcasts:
            if podcast.id in playable_ids:
                return podcast
        return None

    def _active_episode(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> Episode | None:
        if state.current_podcast_id is None:
            return None
        download = downloads.get(state.current_podcast_id)
        if download is None or download.status != "downloaded":
            return None
        return _episode_from_download(download)

    def _playable_podcast_ids(
        self,
        downloads: Mapping[str, DownloadMetadata],
    ) -> frozenset[str]:
        return frozenset(
            podcast_id
            for podcast_id, download in downloads.items()
            if download.status == "downloaded"
        )


def _episode_from_download(download: DownloadMetadata) -> Episode:
    return Episode(
        title=download.title or download.episode_identity,
        identity=download.episode_identity,
        guid=None,
        published_at=None,
        media_url=download.media_url,
        media_type=download.media_type,
        local_file=download.local_file,
        downloaded_at=download.downloaded_at,
    )
