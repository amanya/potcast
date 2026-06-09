# Configuration

Potcast loads one YAML file at startup:

```bash
potcast --config /path/to/potcast.yaml
```

See `examples/potcast.yaml` for a complete starting point.

## Required Fields

- `channels`: list of channel objects.
- `channels[].id`: stable unique channel ID.
- `channels[].name`: display name.
- `channels[].podcasts`: list of podcasts in the channel.
- `channels[].podcasts[].id`: stable unique podcast ID. Duplicate podcast IDs are rejected.
- `channels[].podcasts[].name`: display name.
- `channels[].podcasts[].feed_url`: RSS or Atom feed URL.
- `outputs.primary`: primary backend, either `icecast` or `local_audio`.

## Server

```yaml
server:
  host: "0.0.0.0"
  port: 8080
```

- `host`: HTTP bind address. Default: `0.0.0.0`.
- `port`: HTTP port. Default: `8080`.

## Storage

```yaml
storage:
  data_dir: "/data"
  episodes_dir: "/data/episodes"
```

- `data_dir`: JSON runtime metadata directory. Default: `./data`.
- `episodes_dir`: downloaded media directory. Default: `<data_dir>/episodes`.

Startup creates both directories. Runtime state is stored as `state.json`, `feeds.json`,
and `downloads.json` in `data_dir`.

## Station

```yaml
station:
  start_on_boot: false
  shuffle_podcasts: true
  shuffle_channels: false
  volume: 70
  sleep_timer_minutes: null
```

- `start_on_boot`: call station play during startup. Default: `false`.
- `shuffle_podcasts`: randomize podcast selection within a channel. Default: `true`.
- `shuffle_channels`: randomize channel selection. Default: `false`.
- `volume`: initial station volume from 0 to 100. Default: `100`.
- `sleep_timer_minutes`: reserved optional sleep timer. Default: `null`.

`station.volume` seeds `state.json` on first boot only. After that, persisted runtime state
preserves command changes across restarts.

## Outputs

```yaml
outputs:
  primary: "icecast"
  enabled:
    - "icecast"
```

The primary output must be listed in `enabled`, and the backend section must have
`enabled: true`.

### Icecast

```yaml
outputs:
  icecast:
    enabled: true
    host: "icecast"
    port: 8000
    source_password: "change-me"
    mount: "/potcast.mp3"
    public_url: "http://localhost:8000/potcast.mp3"
    name: "Potcast"
    description: "Personal podcast radio"
    genre: "Podcast"
    format: "mp3"
    bitrate_kbps: 128
    sample_rate_hz: 44100
```

Icecast output uses `ffmpeg` to stream the selected local episode file to the Icecast
source endpoint.

### Local Audio

```yaml
outputs:
  primary: "local_audio"
  enabled:
    - "local_audio"
  local_audio:
    enabled: true
    player: "mpv"
    device: "default"
    mixer: "software"
```

Local audio uses `mpv` by default and is intended for Raspberry Pi or host audio output.

## Feeds

```yaml
feeds:
  refresh_interval_minutes: 30
  download_timeout_seconds: 60
  user_agent: "Potcast/0.1.0"
```

- `refresh_interval_minutes`: scheduler interval. Default: `30`.
- `download_timeout_seconds`: timeout used for feed fetches and media downloads. Default: `60`.
- `user_agent`: HTTP user agent for feed and media requests. Default: `Potcast/0.1.0`.

Feed and download failures preserve the last good local episode.
