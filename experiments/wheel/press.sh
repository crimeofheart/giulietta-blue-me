#!/bin/bash
#
# Bench only: press a steering-wheel button for the running app, with no car.
# The frames go through the MCP2515's internal loopback, never onto a bus, and
# the script refuses if a live bus is attached. They look like the Body
# Computer's (captures/C-buttons.log): the button's bit at the press, repeated
# every 0.5 s while held, then 0000 at the release.
#
#   sudo ~/press.sh phone          short press
#   sudo ~/press.sh phone 1.5      held 1.5 s: a long press
#   sudo ~/press.sh voice next     several buttons, one after the other
#
# Buttons: volume_up volume_down mute next prev source phone voice

set -u
IF=can0
ID=06354000
HELD=0.3

say() { echo "$(date +%T.%N | cut -c1-12) $*"; }

mask() {
    case "$1" in
        volume_up) echo 8000 ;;  volume_down) echo 4000 ;;  mute) echo 2000 ;;
        next) echo 1000 ;;       prev) echo 0800 ;;         source) echo 0400 ;;
        phone) echo 0080 ;;      voice) echo 0040 ;;
        *) return 1 ;;
    esac
}

listen_only() {
    ip link set "$IF" down
    ip link set "$IF" type can bitrate 50000 loopback off listen-only on
    ip link set "$IF" up
    say "restored: $(ip -details link show "$IF" | grep -o '<LISTEN-ONLY>' || echo 'NOT LISTEN-ONLY, CHECK')"
}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
[ $# -ge 1 ] || { sed -n '3,15p' "$0"; exit 1; }

# Buttons, each optionally followed by how long it is held.
presses=()
while [ $# -gt 0 ]; do
    m=$(mask "$1") || { echo "unknown button: $1"; exit 1; }
    held=$HELD
    if [[ ${2:-} =~ ^[0-9.]+$ ]]; then held=$2; shift; fi
    presses+=("$1:$m:$held")
    shift
done

if candump -n 1 -T 2000 "$IF" 2>/dev/null | grep -q .; then
    say "frames on $IF: a live bus is attached; refusing"
    exit 1
fi

trap listen_only EXIT
ip link set "$IF" down
ip link set "$IF" type can bitrate 50000 listen-only off loopback on
ip link set "$IF" up
sleep 1.5   # the app's receive thread notices the link came back

for p in "${presses[@]}"; do
    IFS=: read -r name m held <<< "$p"
    # The 2 Hz repeat while held, worked out before the press so nothing
    # slow (a Python start on the Zero W takes ~0.3 s) sits inside the hold.
    read -r repeats rest < <(awk -v h="$held" 'BEGIN {
        n = int(h / 0.5); if (n > 0 && n * 0.5 >= h) n--
        printf "%d %.3f\n", n, h - n * 0.5 }')
    say "press $name ($m), held $held s"
    cansend "$IF" "$ID#$m"
    for ((i = 0; i < repeats; i++)); do sleep 0.5; cansend "$IF" "$ID#$m"; done
    sleep "$rest"
    cansend "$IF" "$ID#0000"
    say "release $name"
    sleep 1
done
