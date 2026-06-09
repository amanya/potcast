"""Application service for station commands and output coordination."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

from potcast.models import (
    ChannelConfig,
    CommandError,
    DownloadMetadata,
    Episode,
    OutputError,
    OutputPlaybackEvent,
    PlaybackSupervisorStatus,
    PodcastConfig,
    RuntimeState,
    StationCommandResult,
    StationConfig,
    StationStatus,
)
from potcast.outputs.base import OutputBackend
from potcast.station import Randomizer, StationSelector

Clock = Callable[[], datetime]
LOGGER = logging.getLogger(__name__)


class StationStateStore(Protocol):
    """Persistence required by the station service."""

    def load_runtime_state(self) -> RuntimeState: ...

    def save_runtime_state(self, state: RuntimeState) -> None: ...

    def load_download_metadata(self) -> dict[str, DownloadMetadata]: ...


@dataclass(frozen=True)
class OutputRetryPolicy:
    """Bounded retry policy used by the playback supervisor."""

    max_attempts: int = 1
    initial_delay: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("Output retry max_attempts must not be negative.")
        if self.max_attempts > 0 and self.initial_delay.total_seconds() <= 0:
            raise ValueError("Output retry initial_delay must be greater than zero.")

    def next_delay(self, attempt_number: int) -> timedelta | None:
        if attempt_number < 1 or attempt_number > self.max_attempts:
            return None
        return self.initial_delay * attempt_number


@dataclass(frozen=True)
class _OutputRetryState:
    attempts: int = 0
    next_retry_at: datetime | None = None


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
        retry_policy: OutputRetryPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.channels = tuple(channels)
        self.station = station
        self.state_store = state_store
        self.output = output
        self.randomizer = randomizer
        self.retry_policy = retry_policy or OutputRetryPolicy()
        self._clock = clock or _utc_now
        self._retry_state = _OutputRetryState()

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
        if state.station_status != "stopped" or state.playback_supervisor_error is not None:
            state = replace(
                state,
                station_status="stopped",
                playback_supervisor_error=None,
            )
            self._reset_retry()
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
        if self.output.status().state != "error" and state.playback_supervisor_error is None:
            return self._result(state, downloads)

        LOGGER.info(
            "Manually recovering output playback",
            extra=self._log_context(state, downloads),
        )
        self.output.stop()
        self._reset_retry()
        state = self._play_selected(state, downloads, schedule_retry=False)
        if state.playback_supervisor_error is None:
            LOGGER.info(
                "Manual output recovery succeeded",
                extra=self._log_context(state, downloads),
            )
        else:
            LOGGER.warning(
                "Manual output recovery failed",
                extra=self._log_context(
                    state,
                    downloads,
                    error=state.playback_supervisor_error,
                ),
            )
            self._save_state(state)
            return self._result(
                state,
                downloads,
                error=CommandError(
                    code="output_recovery_failed",
                    message=f"Output recovery failed: {state.playback_supervisor_error.message}",
                ),
            )
        self._save_state(state)
        return self._result(state, downloads)

    def advance_if_finished(self) -> StationCommandResult:
        """Advance once when the active output reports normal episode completion."""

        state = self._load_state()
        downloads = self.state_store.load_download_metadata()
        if state.playback_supervisor_error is not None:
            state = self._retry_output_if_due(state, downloads)
            self._save_state(state)
            return self._result(state, downloads)
        if state.station_status != "playing":
            return self._result(state, downloads)
        event = self.output.consume_playback_event()
        if event is None:
            return self._result(state, downloads)
        if event.outcome != "completed":
            error = self._event_error(event)
            self._schedule_retry(error)
            state = replace(
                state,
                station_status="idle",
                playback_supervisor_error=error,
            )
            LOGGER.warning(
                "Output playback failed; station blocked",
                extra=self._log_context(state, downloads, error=error),
            )
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
        *,
        schedule_retry: bool = True,
    ) -> RuntimeState:
        state = self._activate(state, downloads)
        episode = self._active_episode(state, downloads)
        if episode is None:
            self.output.stop()
            return replace(
                state,
                station_status="idle",
                playback_supervisor_error=None,
            )

        self.output.play_episode(episode)
        output_status = self.output.status()
        if output_status.state == "error":
            error = self._output_status_error()
            if schedule_retry:
                self._schedule_retry(error)
            blocked_state = replace(
                state,
                station_status="idle",
                playback_supervisor_error=error,
            )
            LOGGER.warning(
                "Output playback failed to start; station blocked",
                extra=self._log_context(blocked_state, downloads, error=error),
            )
            return blocked_state
        self._reset_retry()
        return replace(state, station_status="playing", playback_supervisor_error=None)

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
            playback_supervisor=self._playback_supervisor_status(state),
        )

    def _playback_supervisor_status(self, state: RuntimeState) -> PlaybackSupervisorStatus:
        if state.playback_supervisor_error is not None:
            supervisor_state = self._blocked_supervisor_state()
            return PlaybackSupervisorStatus(
                state=supervisor_state,
                last_error=state.playback_supervisor_error,
                next_retry_at=self._retry_state.next_retry_at,
                retry_attempts=self._retry_state.attempts,
                max_retry_attempts=self.retry_policy.max_attempts,
            )
        if state.station_status == "playing":
            return PlaybackSupervisorStatus(
                state="watching",
                retry_attempts=self._retry_state.attempts,
                max_retry_attempts=self.retry_policy.max_attempts,
            )
        return PlaybackSupervisorStatus(
            state="idle",
            retry_attempts=self._retry_state.attempts,
            max_retry_attempts=self.retry_policy.max_attempts,
        )

    def _blocked_supervisor_state(self) -> str:
        if self._retry_state.next_retry_at is not None:
            return "retry_scheduled"
        if self._retry_state.attempts >= self.retry_policy.max_attempts:
            return "exhausted"
        return "blocked"

    def _retry_output_if_due(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
    ) -> RuntimeState:
        next_retry_at = self._retry_state.next_retry_at
        if next_retry_at is None or self._clock() < next_retry_at:
            return state

        retry_attempt = self._retry_state.attempts + 1
        LOGGER.info(
            "Retrying output playback",
            extra=self._log_context(
                state,
                downloads,
                error=state.playback_supervisor_error,
                retry_attempt=retry_attempt,
            ),
        )
        self.output.stop()
        self._retry_state = replace(
            self._retry_state,
            attempts=retry_attempt,
            next_retry_at=None,
        )
        state = self._play_selected(state, downloads)
        if state.playback_supervisor_error is None:
            LOGGER.info(
                "Output playback retry succeeded",
                extra=self._log_context(
                    state,
                    downloads,
                    retry_attempt=retry_attempt,
                ),
            )
        return state

    def _schedule_retry(self, error: OutputError) -> None:
        if self._retry_state.next_retry_at is not None:
            return
        next_attempt = self._retry_state.attempts + 1
        delay = self.retry_policy.next_delay(next_attempt)
        if delay is None:
            LOGGER.error(
                "Output playback retry policy exhausted",
                extra={
                    "error_code": error.code,
                    "error_message": error.message,
                    "retry_attempts": self._retry_state.attempts,
                    "max_retry_attempts": self.retry_policy.max_attempts,
                },
            )
            return
        next_retry_at = self._clock() + delay
        self._retry_state = replace(
            self._retry_state,
            next_retry_at=next_retry_at,
        )
        LOGGER.info(
            "Scheduled output playback retry",
            extra={
                "error_code": error.code,
                "error_message": error.message,
                "next_retry_at": next_retry_at.isoformat(),
                "retry_attempt": next_attempt,
                "max_retry_attempts": self.retry_policy.max_attempts,
            },
        )

    def _reset_retry(self) -> None:
        self._retry_state = _OutputRetryState()

    def _event_error(self, event: OutputPlaybackEvent) -> OutputError:
        error = event.error
        if error is not None:
            return error
        status_error = self.output.status().error
        if status_error is not None:
            return status_error
        return OutputError(
            code="backend_process_failed",
            message="Output playback failed without a structured backend error.",
        )

    def _output_status_error(self) -> OutputError:
        error = self.output.status().error
        if error is not None:
            return error
        return OutputError(
            code="backend_process_failed",
            message="Output backend entered error state without a structured error.",
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

    def _log_context(
        self,
        state: RuntimeState,
        downloads: Mapping[str, DownloadMetadata],
        *,
        error: OutputError | None = None,
        retry_attempt: int | None = None,
    ) -> dict[str, object | None]:
        episode = self._active_episode(state, downloads)
        context: dict[str, object | None] = {
            "station_state": state.station_status,
            "channel_id": state.current_channel_id,
            "podcast_id": state.current_podcast_id,
            "episode_identity": episode.identity if episode is not None else None,
            "output_backend": self.output.status().backend,
            "retry_attempts": self._retry_state.attempts,
            "max_retry_attempts": self.retry_policy.max_attempts,
            "next_retry_at": (
                self._retry_state.next_retry_at.isoformat()
                if self._retry_state.next_retry_at is not None
                else None
            ),
        }
        if error is not None:
            context["error_code"] = error.code
            context["error_message"] = error.message
        if retry_attempt is not None:
            context["retry_attempt"] = retry_attempt
        return context


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
