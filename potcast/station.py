"""Pure station selection rules."""

from __future__ import annotations

import random
from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Protocol, TypeVar

from potcast.models import ChannelConfig, PodcastConfig, RuntimeState, StationConfig

T = TypeVar("T")


class Randomizer(Protocol):
    """Random choice dependency used by shuffled station selection."""

    def choice(self, items: Sequence[T]) -> T: ...


class StationSelector:
    """Select active channels and podcasts without infrastructure dependencies."""

    def __init__(
        self,
        channels: Sequence[ChannelConfig],
        station: StationConfig,
        *,
        playable_podcast_ids: Collection[str] | None = None,
        randomizer: Randomizer | None = None,
    ) -> None:
        self.channels = tuple(channels)
        self.station = station
        self.playable_podcast_ids = (
            frozenset(playable_podcast_ids) if playable_podcast_ids is not None else None
        )
        self.randomizer = randomizer or random.Random()

    def activate(self, state: RuntimeState) -> RuntimeState:
        """Ensure the state points at a configured channel and playable podcast when possible."""

        channel = self._active_channel(state)
        if channel is None:
            return replace(state, current_channel_id=None, current_podcast_id=None)

        podcast = self._active_podcast(channel, state)
        if podcast is not None:
            return replace(
                state,
                current_channel_id=channel.id,
                current_podcast_id=podcast.id,
            )

        first_podcast = self._first_playable_podcast(channel)
        return replace(
            state,
            current_channel_id=channel.id,
            current_podcast_id=first_podcast.id if first_podcast is not None else None,
        )

    def next_podcast(self, state: RuntimeState) -> RuntimeState:
        """Move to the next playable podcast in the active channel."""

        requested_podcast_id = state.current_podcast_id
        state = self.activate(state)
        channel = self._active_channel(state)
        if channel is None:
            return state

        playable = self._playable_podcasts(channel)
        if not playable:
            return replace(state, current_podcast_id=None, previous_podcast_ids=())

        if state.current_podcast_id is None:
            return replace(state, current_podcast_id=playable[0].id)
        if requested_podcast_id != state.current_podcast_id:
            return state

        if self.station.shuffle_podcasts:
            return self._next_shuffled_podcast(state, playable)

        next_podcast = self._offset_podcast(playable, state.current_podcast_id, offset=1)
        return replace(
            state,
            current_podcast_id=next_podcast.id,
            previous_podcast_ids=_append_history(
                state.previous_podcast_ids,
                state.current_podcast_id,
            ),
        )

    def previous_podcast(self, state: RuntimeState) -> RuntimeState:
        """Move back through selection history, or configured order when history is empty."""

        requested_podcast_id = state.current_podcast_id
        state = self.activate(state)
        channel = self._active_channel(state)
        if channel is None:
            return state

        playable = self._playable_podcasts(channel)
        if not playable:
            return replace(state, current_podcast_id=None, previous_podcast_ids=())

        history = list(state.previous_podcast_ids)
        while history:
            podcast_id = history.pop()
            if podcast_id in {podcast.id for podcast in playable}:
                return replace(
                    state,
                    current_podcast_id=podcast_id,
                    previous_podcast_ids=tuple(history),
                )

        if state.current_podcast_id is None:
            return replace(state, current_podcast_id=playable[0].id)
        if requested_podcast_id != state.current_podcast_id:
            return state

        previous = self._offset_podcast(playable, state.current_podcast_id, offset=-1)
        return replace(state, current_podcast_id=previous.id)

    def next_channel(self, state: RuntimeState) -> RuntimeState:
        """Move to the next configured channel."""

        return self._offset_channel(state, offset=1)

    def previous_channel(self, state: RuntimeState) -> RuntimeState:
        """Move to the previous configured channel."""

        return self._offset_channel(state, offset=-1)

    def _next_shuffled_podcast(
        self,
        state: RuntimeState,
        playable: Sequence[PodcastConfig],
    ) -> RuntimeState:
        current_id = state.current_podcast_id
        if current_id is None:
            return replace(state, current_podcast_id=playable[0].id)

        playable_ids = tuple(podcast.id for podcast in playable)
        if len(playable_ids) == 1:
            return replace(state, current_podcast_id=playable_ids[0])

        already_played = set(state.previous_podcast_ids)
        already_played.add(current_id)
        candidates = [podcast_id for podcast_id in playable_ids if podcast_id not in already_played]
        history = state.previous_podcast_ids

        if not candidates:
            candidates = [podcast_id for podcast_id in playable_ids if podcast_id != current_id]
            history = ()

        selected_id = self.randomizer.choice(candidates)
        return replace(
            state,
            current_podcast_id=selected_id,
            previous_podcast_ids=_append_history(history, current_id),
        )

    def _offset_channel(self, state: RuntimeState, *, offset: int) -> RuntimeState:
        if not self.channels:
            return replace(state, current_channel_id=None, current_podcast_id=None)

        state = self.activate(state)
        channel_ids = tuple(channel.id for channel in self.channels)
        try:
            current_index = channel_ids.index(state.current_channel_id)
        except ValueError:
            current_index = 0

        channel = self.channels[(current_index + offset) % len(self.channels)]
        podcast = self._first_playable_podcast(channel)
        return replace(
            state,
            current_channel_id=channel.id,
            current_podcast_id=podcast.id if podcast is not None else None,
            previous_podcast_ids=(),
        )

    def _active_channel(self, state: RuntimeState) -> ChannelConfig | None:
        if not self.channels:
            return None

        if state.current_channel_id is not None:
            for channel in self.channels:
                if channel.id == state.current_channel_id:
                    return channel

        return self.channels[0]

    def _active_podcast(
        self,
        channel: ChannelConfig,
        state: RuntimeState,
    ) -> PodcastConfig | None:
        if state.current_podcast_id is None:
            return None

        for podcast in channel.podcasts:
            if podcast.id == state.current_podcast_id and self._is_playable(podcast):
                return podcast
        return None

    def _first_playable_podcast(self, channel: ChannelConfig) -> PodcastConfig | None:
        playable = self._playable_podcasts(channel)
        if not playable:
            return None
        return playable[0]

    def _playable_podcasts(self, channel: ChannelConfig) -> tuple[PodcastConfig, ...]:
        return tuple(podcast for podcast in channel.podcasts if self._is_playable(podcast))

    def _is_playable(self, podcast: PodcastConfig) -> bool:
        if self.playable_podcast_ids is None:
            return True
        return podcast.id in self.playable_podcast_ids

    @staticmethod
    def _offset_podcast(
        podcasts: Sequence[PodcastConfig],
        current_podcast_id: str,
        *,
        offset: int,
    ) -> PodcastConfig:
        podcast_ids = tuple(podcast.id for podcast in podcasts)
        try:
            current_index = podcast_ids.index(current_podcast_id)
        except ValueError:
            current_index = 0
        return podcasts[(current_index + offset) % len(podcasts)]


def _append_history(history: tuple[str, ...], podcast_id: str) -> tuple[str, ...]:
    if history and history[-1] == podcast_id:
        return history
    return (*history, podcast_id)
