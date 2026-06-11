#!/usr/bin/env bash
set -euo pipefail

# Reproduce the Icecast -> rpitx/pifmrds FM relay setup.
# Run on a Raspberry Pi with sudo access, e.g.:
#   chmod +x install-rpitx-icecast-fm.sh
#   ./install-rpitx-icecast-fm.sh
# Override defaults if needed, e.g.:
#   STREAM_URL="http://192.168.68.5:8022/potcast.mp3" FREQ_MHZ="107.9" ./install-rpitx-icecast-fm.sh

STREAM_URL="${STREAM_URL:-http://192.168.68.5:8022/potcast.mp3}"
FREQ_MHZ="${FREQ_MHZ:-107.9}"
PS="${PS:-POTCAST}"
RT="${RT:-Icecast stream from 192.168.68.5:8022/potcast.mp3}"
PPM="${PPM:-0}"
TIMEZONE="${TIMEZONE:-Europe/Madrid}"
AUDIO_CHANNELS="${AUDIO_CHANNELS:-1}"  # 1=mono, 2=stereo
AUDIO_FILTER="${AUDIO_FILTER:-acompressor=threshold=0.08:ratio=4:attack=5:release=100:makeup=14,alimiter=limit=0.99}"
RESTART_DELAY="${RESTART_DELAY:-5}"
ENABLE_SERVICE="${ENABLE_SERVICE:-1}"
MAKE_JOBS="${MAKE_JOBS:-1}"
RPITX_DIR="${RPITX_DIR:-$HOME/rpitx}"

q() { printf '%q' "$1"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }
}

if ! sudo -n true 2>/dev/null; then
  echo "This installer needs passwordless sudo or an active sudo session." >&2
  echo "Try: sudo -v && ./install-rpitx-icecast-fm.sh" >&2
  exit 1
fi

need_cmd git

echo "==> Installing packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git build-essential ca-certificates pkg-config \
  ffmpeg sox libsndfile1-dev libfftw3-dev \
  imagemagick rtl-sdr buffer libbcm2835-dev

if command -v timedatectl >/dev/null 2>&1; then
  echo "==> Setting timezone/NTP: ${TIMEZONE}"
  sudo timedatectl set-timezone "${TIMEZONE}"
  sudo timedatectl set-ntp true || true
fi

echo "==> Getting rpitx"
if [ -d "${RPITX_DIR}/.git" ]; then
  git -C "${RPITX_DIR}" pull --ff-only || true
else
  rm -rf "${RPITX_DIR}"
  git clone https://github.com/F5OEO/rpitx.git "${RPITX_DIR}"
fi

echo "==> Building/installing librpitx"
LIBRPITX_DIR="${RPITX_DIR}/src/librpitx"
if [ -d "${LIBRPITX_DIR}/.git" ]; then
  git -C "${LIBRPITX_DIR}" pull --ff-only || true
else
  rm -rf "${LIBRPITX_DIR}"
  git clone https://github.com/F5OEO/librpitx "${LIBRPITX_DIR}"
fi

# Raspberry Pi OS trixie may not ship /opt/vc/lib/libbcm_host.
# F5OEO/librpitx provides the needed bcm_host_* functions in rpi.c, so build without -lbcm_host.
make -C "${LIBRPITX_DIR}/src" clean || true
make -C "${LIBRPITX_DIR}/src" -j"${MAKE_JOBS}" LDFLAGS="-lm -lrt -lpthread -fPIC"
sudo make -C "${LIBRPITX_DIR}/src" install
sudo ldconfig || true

echo "==> Building/installing pifmrds"
make -C "${RPITX_DIR}/src" -j"${MAKE_JOBS}" ../pifmrds
sudo install -m 0755 "${RPITX_DIR}/pifmrds" /usr/local/bin/pifmrds

echo "==> Installing relay script"
sudo tee /usr/local/bin/icecast-fm >/dev/null <<'EOF'
#!/usr/bin/env bash
set -u

if [ -f /etc/default/icecast-fm ]; then
  # shellcheck disable=SC1091
  . /etc/default/icecast-fm
fi

: "${STREAM_URL:?STREAM_URL is required}"
: "${FREQ_MHZ:?FREQ_MHZ is required}"
: "${PS:=POTCAST}"
: "${RT:=Icecast FM relay}"
: "${PPM:=0}"
: "${RESTART_DELAY:=5}"
: "${AUDIO_CHANNELS:=1}"
: "${VOLUME:=1.0}"
: "${AUDIO_FILTER:=volume=${VOLUME}}"

cleanup() {
  echo "Stopping Icecast FM relay..."
  pkill -TERM -P $$ 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup TERM INT EXIT

while true; do
  echo "Starting Icecast FM relay: ${STREAM_URL} -> ${FREQ_MHZ} MHz"
  echo "Audio filter: ${AUDIO_FILTER}; channels: ${AUDIO_CHANNELS}"

  ffmpeg -hide_banner -loglevel warning -nostdin \
    -reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_delay_max 10 \
    -i "${STREAM_URL}" \
    -vn -af "${AUDIO_FILTER}" \
    -f wav -acodec pcm_s16le -ar 44100 -ac "${AUDIO_CHANNELS}" - \
  | /usr/local/bin/pifmrds \
      -freq "${FREQ_MHZ}" \
      -audio - \
      -ps "${PS}" \
      -rt "${RT}" \
      -ppm "${PPM}"

  status=$?
  echo "Relay stopped with status ${status}; restarting in ${RESTART_DELAY}s"
  sleep "${RESTART_DELAY}"
done
EOF
sudo chmod 0755 /usr/local/bin/icecast-fm

echo "==> Writing /etc/default/icecast-fm"
{
  echo '# Icecast-to-FM configuration'
  echo '# Transmit only on frequencies/power levels you are authorized to use.'
  echo "STREAM_URL=$(q "$STREAM_URL")"
  echo "FREQ_MHZ=$(q "$FREQ_MHZ")"
  echo "PS=$(q "$PS")"
  echo "RT=$(q "$RT")"
  echo "PPM=$(q "$PPM")"
  echo "RESTART_DELAY=$(q "$RESTART_DELAY")"
  echo "AUDIO_CHANNELS=$(q "$AUDIO_CHANNELS")"
  echo "AUDIO_FILTER=$(q "$AUDIO_FILTER")"
} | sudo tee /etc/default/icecast-fm >/dev/null

echo "==> Installing systemd service"
sudo tee /etc/systemd/system/icecast-fm.service >/dev/null <<'EOF'
[Unit]
Description=Icecast stream to FM transmitter using rpitx/pifmrds
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=/etc/default/icecast-fm
ExecStart=/usr/local/bin/icecast-fm
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

echo "==> Applying rpitx boot stability settings"
BOOT_CONFIG="/boot/firmware/config.txt"
[ -f "${BOOT_CONFIG}" ] || BOOT_CONFIG="/boot/config.txt"
if [ -f "${BOOT_CONFIG}" ]; then
  for line in gpu_freq=250 force_turbo=1; do
    grep -qF "${line}" "${BOOT_CONFIG}" || echo "${line}" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
  done
  echo "Updated ${BOOT_CONFIG}"
else
  echo "Warning: could not find /boot/firmware/config.txt or /boot/config.txt" >&2
fi

if [ "${ENABLE_SERVICE}" = "1" ]; then
  echo "==> Enabling and starting icecast-fm.service"
  sudo systemctl enable --now icecast-fm.service
else
  echo "==> Service installed but not enabled. Start with: sudo systemctl enable --now icecast-fm.service"
fi

echo
echo "Done. Current config:"
sudo sed -n '1,120p' /etc/default/icecast-fm

echo
echo "Useful commands:"
echo "  sudo systemctl status icecast-fm.service --no-pager -l"
echo "  journalctl -u icecast-fm.service -f"
echo "  sudo systemctl restart icecast-fm.service"
echo "  sudo systemctl stop icecast-fm.service"
echo
echo "A reboot is recommended after first install so gpu_freq/force_turbo take effect."
