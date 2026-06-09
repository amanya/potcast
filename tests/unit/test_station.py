from __future__ import annotations

from collections.abc import Sequence

from potcast.models import ChannelConfig, PodcastConfig, RuntimeState, StationConfig
from potcast.station import StationSelector


class QueueRandomizer:
    def __init__(self, choices: Sequence[str]) -> None:
        self.choices = list(choices)

    def choice(self, items: Sequence[str]) -> str:
        for choice in self.choices:
            if choice in items:
                self.choices.remove(choice)
                return choice
        return items[0]


def podcast(podcast_id: str) -> PodcastConfig:
    return PodcastConfig(
        id=podcast_id,
        name=podcast_id.replace("-", " ").title(),
        feed_url=f"https://example.com/{podcast_id}.xml",
    )


def channel(channel_id: str, podcast_ids: Sequence[str]) -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name=channel_id.title(),
        podcasts=tuple(podcast(podcast_id) for podcast_id in podcast_ids),
    )


def selector(
    *,
    shuffle: bool = False,
    playable: set[str] | None = None,
    choices: Sequence[str] = (),
) -> StationSelector:
    return StationSelector(
        (
            channel("sleep", ("history-extra", "science-hour", "short-fiction")),
            channel("stories", ("myths", "interviews")),
            channel("empty", ("offline",)),
        ),
        StationConfig(shuffle_podcasts=shuffle),
        playable_podcast_ids=playable,
        randomizer=QueueRandomizer(choices),
    )


def test_next_podcast_in_sequential_mode() -> None:
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="history-extra")

    result = selector().next_podcast(state)

    assert result.current_channel_id == "sleep"
    assert result.current_podcast_id == "science-hour"
    assert result.previous_podcast_ids == ("history-extra",)


def test_previous_podcast_in_sequential_mode() -> None:
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="science-hour")

    result = selector().previous_podcast(state)

    assert result.current_podcast_id == "history-extra"


def test_previous_podcast_uses_history_before_configured_order() -> None:
    state = RuntimeState(
        current_channel_id="sleep",
        current_podcast_id="short-fiction",
        previous_podcast_ids=("history-extra",),
    )

    result = selector().previous_podcast(state)

    assert result.current_podcast_id == "history-extra"
    assert result.previous_podcast_ids == ()


def test_shuffle_avoids_immediate_repeats_where_possible() -> None:
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="history-extra")

    result = selector(shuffle=True, choices=("history-extra", "science-hour")).next_podcast(state)

    assert result.current_podcast_id == "science-hour"
    assert result.current_podcast_id != state.current_podcast_id


def test_shuffle_eventually_covers_all_playable_podcasts_before_resetting() -> None:
    shuffled = selector(shuffle=True, choices=("science-hour", "short-fiction", "history-extra"))
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="history-extra")

    state = shuffled.next_podcast(state)
    assert state.current_podcast_id == "science-hour"

    state = shuffled.next_podcast(state)
    assert state.current_podcast_id == "short-fiction"

    state = shuffled.next_podcast(state)
    assert state.current_podcast_id == "history-extra"
    assert state.previous_podcast_ids == ("short-fiction",)


def test_previous_uses_history_in_shuffle_mode() -> None:
    state = RuntimeState(
        current_channel_id="sleep",
        current_podcast_id="short-fiction",
        previous_podcast_ids=("history-extra", "science-hour"),
    )

    result = selector(shuffle=True).previous_podcast(state)

    assert result.current_podcast_id == "science-hour"
    assert result.previous_podcast_ids == ("history-extra",)


def test_channel_next_and_previous_follow_configured_order() -> None:
    station = selector()
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="history-extra")

    next_channel = station.next_channel(state)
    previous_channel = station.previous_channel(next_channel)

    assert next_channel.current_channel_id == "stories"
    assert next_channel.current_podcast_id == "myths"
    assert previous_channel.current_channel_id == "sleep"
    assert previous_channel.current_podcast_id == "history-extra"


def test_unavailable_podcasts_are_skipped_gracefully() -> None:
    state = RuntimeState(current_channel_id="sleep", current_podcast_id="history-extra")

    result = selector(playable={"short-fiction"}).next_podcast(state)

    assert result.current_channel_id == "sleep"
    assert result.current_podcast_id == "short-fiction"
    assert result.previous_podcast_ids == ()


def test_empty_or_unavailable_channels_idle_gracefully() -> None:
    station = selector(playable={"history-extra"})
    state = RuntimeState(current_channel_id="stories", current_podcast_id="myths")

    result = station.activate(state)

    assert result.current_channel_id == "stories"
    assert result.current_podcast_id is None


def test_selector_without_channels_idles_gracefully() -> None:
    station = StationSelector((), StationConfig())

    result = station.next_podcast(RuntimeState(current_channel_id="missing"))

    assert result.current_channel_id is None
    assert result.current_podcast_id is None
