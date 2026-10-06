#!/bin/bash
#
# Bench only: the installed app as node 4021 on vcan0, fed the car's side of a
# capture, recording what it answers and when. No car, no can0: a second app
# instance runs on vcan0 in gated-tx with node 4021's frames allowed, and with
# --no-phone unless PHONE=1, so replayed radio requests leave the phone alone.
# The installed service keeps running on can0.
#
#   sudo ~/app_replay.sh /tmp/zb-car.log      -> /tmp/app-replay.log (candump -L)
#   sudo PHONE=1 ~/app_replay.sh /tmp/zb-car.log   the phone plays when the radio
#                                                   asks, and its track is sent
#
# The replay log is candump -L text for can0, with node 4021's own frames and
# any wheel presses already taken out (they are the app's to make, and the
# phone's to hear).

set -u
IN=${1:?replay log}
OUT=${OUT:-/tmp/app-replay.log}
CFG=/tmp/app-replay.yaml
UNIT=app-replay

say() { echo "$(date +%T) $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
modprobe vcan
ip link show vcan0 >/dev/null 2>&1 || ip link add dev vcan0 type vcan
ip link set up vcan0

/opt/blueandme/.venv/bin/python - "$CFG" <<'EOF'
import sys, yaml
cfg = yaml.safe_load(open("/etc/blueandme/config.yaml"))
cfg["can"].update(interface="vcan0", mode="gated-tx", allowed_tx_frames=[
    "bm_status_response", "bm_proxi_response", "bm_watchdog",
    "bm_track_time", "bm_audio_channel", "bm_text_message"])
yaml.safe_dump(cfg, open(sys.argv[1], "w"))
EOF

cleanup() { systemctl stop "$UNIT" 2>/dev/null; kill "$DUMP" 2>/dev/null; }
trap cleanup EXIT
systemctl reset-failed "$UNIT" 2>/dev/null
systemd-run --unit="$UNIT" --collect \
    /opt/blueandme/.venv/bin/blueandme-giulietta --config "$CFG" \
    $([ "${PHONE:-0}" = 1 ] || echo --no-phone)
say "waiting for the app to open vcan0"
for _ in $(seq 60); do
    journalctl -u "$UNIT" -o cat --no-pager 2>/dev/null | grep -q "opened vcan0" && break
    sleep 1
done
candump -ta -L vcan0 > "$OUT" &
DUMP=$!
sleep 1
say "replaying $(wc -l < "$IN") frames"
canplayer -I "$IN" vcan0=can0
sleep 4
say "done: $(wc -l < "$OUT") frames in $OUT"
journalctl -u "$UNIT" -o cat --no-pager | grep -E "node|not sending|REFUSED|Error|Traceback" | head -20
