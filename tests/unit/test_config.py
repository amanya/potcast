from __future__ import annotations

from pathlib import Path

import pytest

from potcast.config import load_config, parse_config
from potcast.errors import ConfigError


def minimal_config() -> dict[str, object]:
    return {
        "channels": [
            {
                "id": "sleep",
                "name": "Sleep",
                "podcasts": [
                    {
                        "id": "history-extra",
                        "name": "History Extra",
                        "feed_url": "https://example.com/history-extra/rss",
                    }
                ],
            }
        ]
    }


def test_minimal_config_loads_with_defaults() -> None:
    config = parse_config(minimal_config())

    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8080
    assert config.storage.data_dir == Path("./data")
    assert config.storage.episodes_dir == Path("data/episodes")
    assert config.station.start_on_boot is False
    assert config.station.shuffle_podcasts is True
    assert config.station.volume == 100
    assert config.outputs.primary == "icecast"
    assert config.outputs.enabled == ("icecast",)
    assert config.outputs.icecast.enabled is True
    assert config.outputs.local_audio.enabled is False
    assert config.feeds.refresh_interval_minutes == 30
    assert config.channels[0].podcasts[0].id == "history-extra"


def test_full_config_loads_explicit_values() -> None:
    raw = minimal_config()
    raw.update(
        {
            "server": {"host": "127.0.0.1", "port": 9000},
            "storage": {"data_dir": "/data", "episodes_dir": "/episodes"},
            "station": {
                "start_on_boot": True,
                "shuffle_podcasts": False,
                "shuffle_channels": True,
                "volume": 70,
                "sleep_timer_minutes": 45,
            },
            "outputs": {
                "primary": "local_audio",
                "enabled": ["local_audio"],
                "local_audio": {"enabled": True, "player": "mpv", "device": "alsa/default"},
                "icecast": {"enabled": False},
            },
            "feeds": {
                "refresh_interval_minutes": 15,
                "download_timeout_seconds": 30,
                "user_agent": "PotcastTest/1.0",
            },
        }
    )

    config = parse_config(raw)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9000
    assert config.storage.data_dir == Path("/data")
    assert config.storage.episodes_dir == Path("/episodes")
    assert config.station.start_on_boot is True
    assert config.station.shuffle_podcasts is False
    assert config.station.shuffle_channels is True
    assert config.station.volume == 70
    assert config.station.sleep_timer_minutes == 45
    assert config.outputs.primary == "local_audio"
    assert config.outputs.enabled == ("local_audio",)
    assert config.outputs.local_audio.enabled is True
    assert config.outputs.icecast.enabled is False
    assert config.feeds.user_agent == "PotcastTest/1.0"


def test_load_config_reads_yaml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
channels:
  - id: sleep
    name: Sleep
    podcasts:
      - id: history-extra
        name: History Extra
        feed_url: https://example.com/history-extra/rss
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.channels[0].id == "sleep"


def test_load_config_accepts_utf8_unicode_text(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
channels:
  - id: nit
    name: "Ràdio de la nit"
    podcasts:
      - id: historia
        name: "Història i ciència"
        feed_url: "https://example.com/historia/rss"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.channels[0].name == "Ràdio de la nit"
    assert config.channels[0].podcasts[0].name == "Història i ciència"


def test_load_config_reports_yaml_parse_location_and_tab_hint(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (
            "channels:\n"
            "  - id: sleep\n"
            "    name: Sleep\n"
            "    podcasts:\n"
            "      - id: history-extra\n"
            "        name: History Extra\n"
            '\tfeed_url: "https://example.com/history-extra/rss"\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)

    message = str(exc_info.value)
    assert f"Could not parse YAML config: {config_path}" in message
    assert "line 7, column 1" in message
    assert "YAML indentation must use spaces, not tabs" in message


def test_example_config_loads() -> None:
    config_path = Path(__file__).resolve().parents[2] / "examples" / "potcast.yaml"

    config = load_config(config_path)

    assert config.outputs.primary == "icecast"
    assert config.storage.data_dir == Path("/data")
    assert [channel.id for channel in config.channels] == ["sleep", "stories"]


def test_duplicate_channel_ids_fail() -> None:
    raw = minimal_config()
    raw["channels"] = [
        {
            "id": "sleep",
            "name": "Sleep",
            "podcasts": [{"id": "one", "name": "One", "feed_url": "https://example.com/one"}],
        },
        {
            "id": "sleep",
            "name": "Sleep Again",
            "podcasts": [{"id": "two", "name": "Two", "feed_url": "https://example.com/two"}],
        },
    ]

    with pytest.raises(ConfigError, match="Duplicate channel id: sleep"):
        parse_config(raw)


def test_duplicate_podcast_ids_fail() -> None:
    raw = minimal_config()
    raw["channels"] = [
        {
            "id": "sleep",
            "name": "Sleep",
            "podcasts": [{"id": "same", "name": "One", "feed_url": "https://example.com/one"}],
        },
        {
            "id": "stories",
            "name": "Stories",
            "podcasts": [{"id": "same", "name": "Two", "feed_url": "https://example.com/two"}],
        },
    ]

    with pytest.raises(ConfigError, match="Duplicate podcast id: same"):
        parse_config(raw)


def test_missing_channels_fail() -> None:
    with pytest.raises(ConfigError, match="Missing required field: channels"):
        parse_config({})


def test_empty_channels_fail() -> None:
    with pytest.raises(ConfigError, match="channels must contain at least one channel"):
        parse_config({"channels": []})


def test_channel_without_podcasts_fails() -> None:
    with pytest.raises(ConfigError, match=r"channels\[0\]\.podcasts must contain at least one"):
        parse_config({"channels": [{"id": "sleep", "name": "Sleep", "podcasts": []}]})


def test_invalid_primary_output_fails() -> None:
    raw = minimal_config()
    raw["outputs"] = {"primary": "airplay"}

    with pytest.raises(ConfigError, match="outputs.primary must be one of"):
        parse_config(raw)


def test_primary_output_must_be_enabled() -> None:
    raw = minimal_config()
    raw["outputs"] = {
        "primary": "local_audio",
        "enabled": ["icecast"],
        "local_audio": {"enabled": True},
    }

    with pytest.raises(ConfigError, match="outputs.primary must be included"):
        parse_config(raw)


def test_enabled_output_config_must_be_enabled() -> None:
    raw = minimal_config()
    raw["outputs"] = {
        "primary": "local_audio",
        "enabled": ["local_audio"],
        "local_audio": {"enabled": False},
    }

    with pytest.raises(ConfigError, match="outputs.local_audio.enabled must be true"):
        parse_config(raw)


def test_invalid_volume_fails() -> None:
    raw = minimal_config()
    raw["station"] = {"volume": 101}

    with pytest.raises(ConfigError, match="station.volume must be between 0 and 100"):
        parse_config(raw)


def test_invalid_server_port_fails() -> None:
    raw = minimal_config()
    raw["server"] = {"port": 0}

    with pytest.raises(ConfigError, match="server.port must be between 1 and 65535"):
        parse_config(raw)
