# Configuration

Potcast loads one YAML file at startup:

```bash
potcast --config /path/to/potcast.yaml
```

See `examples/potcast.yaml` for a complete starting point.

Config files must be UTF-8 YAML. Unicode display names are supported, but YAML
indentation must use spaces; tabs in indentation are rejected by the YAML parser and
reported with the failing line and column.

## Required Fields

- `channels`: list of channel objects.
- `channels[].id`: stable unique channel ID.
- `channels[].name`: display name.
- `channels[].podcasts`: list of podcasts in the channel.
- `channels[].podcasts[].id`: stable unique podcast ID. Duplicate podcast IDs are rejected.
- `channels[].podcasts[].name`: display name.
- `channels[].podcasts[].feed_url`: RSS or Atom feed URL.
- `outputs.primary`: primary backend, either `icecast` or `local_audio`.

Potcast also validates that at least one channel exists, every channel has at least one
podcast, the primary output is listed in `outputs.enabled`, and the selected backend has
`enabled: true`.

## Channels And Podcasts

```yaml
channels:
  - id: "sleep"
    name: "Sleep"
    podcasts:
      - id: "history-extra"
        name: "History Extra"
        feed_url: "https://example.com/history-extra/rss"
```

Channel IDs and podcast IDs are persisted in runtime state, so treat them as stable
identifiers. Rename the display `name` freely, but changing an `id` makes Potcast treat
that channel or podcast as a new item. Podcast IDs must be unique across all channels in
the current implementation.

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
and `downloads.json` in `data_dir`. `state.json` includes the selected station state and
may include a structured `playback_supervisor_error` after an output failure blocks
automatic playback advance. Automatic output retry timing is kept in memory and exposed
through `/status`; it is not written to `state.json`. Because retry timing is in memory,
`playback_supervisor.state` can report `retry_scheduled` before a delayed retry,
`exhausted` after retry attempts are spent, or `blocked` when operator action is needed,
such as after a restart when only the persisted error remains or after a failed manual
recovery attempt.

Downloaded media is stored below `episodes_dir` in one directory per podcast. Final file
names are derived from the podcast ID, a hash of the episode identity, and the media
extension. Temporary files are written beside the final file before replacement.

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
- `shuffle_channels`: reserved for future randomized channel selection. Default: `false`.
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

Only `outputs.primary` is instantiated by the current runtime. `outputs.enabled` is
validated so the config shape can grow toward multiple outputs later, but it does not
start secondary backends today.

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

Icecast output uses `ffmpeg` to stream local episode audio to the Icecast source
endpoint. Potcast keeps the Icecast source connection open while swapping decoded
episode audio underneath it, so browser and radio clients can stay connected when
commands such as `/next`, `/previous`, `/channel/<id>`, or `/podcast/<id>` select a new
episode. Potcast builds an `icecast://source:<password>@<host>:<port><mount>`
destination, applies software volume with an ffmpeg audio filter, and passes stream
metadata such as name, description, genre, format, bitrate, and sample rate.

`source_password` should match the Icecast service source password. `public_url` is the
listener-facing URL; it is useful documentation for clients even though the current HTTP
API does not expose a dedicated stream endpoint yet.

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
The command includes `--no-video`, `--no-terminal`, `--volume=<level>`, the configured
audio device, and `--softvol=yes` when `mixer` is `software`.

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

Runtime startup starts a periodic scheduler. The scheduler triggers a refresh
immediately, then again every `refresh_interval_minutes`. Manual `GET /feeds/refresh`
uses the same refresh service and returns before downloads finish. If a refresh is
already running, the trigger returns `accepted: false` with reason `already_running`.

Supported enclosure media types are:

- `audio/mpeg`
- `audio/mp3`
- `audio/mp4`
- `audio/x-m4a`
- `audio/aac`
- `audio/ogg`

## Episode Retention

Set `channels[].retention_days` to a positive integer to keep episodes published within
that many days (a rolling UTC window, inclusive of its boundary). A podcast's
`retention_days` overrides its channel's value; omitted or `null` inherits the channel.
If neither has a value, only the latest episode is kept, as before.

```yaml
channels:
  - id: history
    name: History
    retention_days: 14
    podcasts:
      - id: twice-weekly
        name: Twice Weekly
        feed_url: "https://example.com/twice-weekly/rss"
      - id: weekly
        name: Weekly
        feed_url: "https://example.com/weekly/rss"
        retention_days: 21
```

On the next refresh, Potcast downloads all playable episodes in the window still
available in the RSS feed, deduplicated by GUID or enclosure URL. Already downloaded
episodes remain eligible even if removed from the feed. The latest downloaded episode
is always kept, even outside the window. Undated episodes are kept only when selected
as the latest episode; other undated feed entries are excluded from the window.

Podcast rotation (including shuffle) stays unchanged. Each visit to a podcast selects
its next retained episode from oldest to newest, wrapping around after the newest.
Manual next and automatic completion both advance the rotation. Selecting a podcast or
channel also starts the next episode for that podcast; previous navigates podcast
history and selects that podcast's next episode, rather than undoing episode history.
Play, pause/resume, output recovery, and retries retain the selected episode. The current
episode identity and per-podcast rotation cursors persist across restarts. This is a
continuous radio rotation, not a permanent listened/unlistened queue.

Cleanup runs after a successful refresh and successful downloads. Feed/download failures
preserve the previous collection. The currently selected episode is protected from
cleanup until selection changes, including while paused or stopped. Removing retention
returns to latest-only on the next successful refresh, with that same protection.
Media downloads are validated and atomically moved before collection metadata is saved;
obsolete files are then removed. A file cleanup failure is logged and may leave an
unused file on disk. Publication dates determine retention, not download time.
