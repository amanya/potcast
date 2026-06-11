# Raspberry Pi Icecast FM Relay

This extra documents and installs a Raspberry Pi relay that receives Potcast from
Icecast and retransmits it with `rpitx`/`pifmrds` for nearby FM receivers.

The default installer values reproduce the current setup:

- Icecast URL: `http://192.168.68.5:8022/potcast.mp3`
- FM frequency: `107.9`
- Mono audio
- Compression/gain filter:
  `acompressor=threshold=0.08:ratio=4:attack=5:release=100:makeup=14,alimiter=limit=0.99`
- RDS enabled with `PS=POTCAST`
- Timezone: `Europe/Madrid`
- systemd service: `icecast-fm.service`
- rpitx boot stability settings: `gpu_freq=250` and `force_turbo=1`

Transmit only on frequencies and power levels you are authorized to use.

## Install

Copy the installer to a new Raspberry Pi and run it from an active sudo session:

```bash
scp extras/rpitx-icecast-fm/install-rpitx-icecast-fm.sh pi@raspberrypi.local:/home/pi/
ssh pi@raspberrypi.local
chmod +x install-rpitx-icecast-fm.sh
sudo -v
./install-rpitx-icecast-fm.sh
```

The installer installs packages, builds `rpitx` and `pifmrds`, writes
`/usr/local/bin/icecast-fm`, writes `/etc/default/icecast-fm`, installs
`/etc/systemd/system/icecast-fm.service`, enables the service by default, and applies the
rpitx boot stability settings.

A reboot is recommended after the first install so boot configuration changes take
effect.

## Overrides

Set environment variables before running the installer:

```bash
FREQ_MHZ="88.1" PS="MYRADIO" ./install-rpitx-icecast-fm.sh
```

Common overrides:

- `STREAM_URL`: Icecast listener URL to relay.
- `FREQ_MHZ`: FM transmit frequency.
- `PS`: RDS program service name.
- `RT`: RDS radio text.
- `PPM`: transmitter frequency correction.
- `TIMEZONE`: timezone passed to `timedatectl`.
- `AUDIO_CHANNELS`: `1` for mono or `2` for stereo.
- `AUDIO_FILTER`: ffmpeg audio filter chain.
- `RESTART_DELAY`: seconds before restarting after relay exit.
- `ENABLE_SERVICE`: set to `0` to install without enabling the service.
- `MAKE_JOBS`: build parallelism.
- `RPITX_DIR`: local `rpitx` checkout path.

## Operations

```bash
sudo systemctl status icecast-fm.service --no-pager -l
journalctl -u icecast-fm.service -f
sudo systemctl restart icecast-fm.service
sudo systemctl stop icecast-fm.service
```
