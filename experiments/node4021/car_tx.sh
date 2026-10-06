#!/bin/bash
#
# Supervised, at the car only: let node4021_full.py transmit, so the radio
# offers and selects the Blue&Me source (step 14 audio test). can0 transmits
# for the run and goes back to the mode it was in on every exit, including
# Ctrl-C. A listen-only app keeps running: it turns the wheel buttons into
# phone actions. An app that answers as node 4021 itself (`tx.sh on`) is
# stopped for the run, two senders of 4021 would contend, and started again
# after (wheel buttons off meanwhile).
#
#   sudo ~/car_tx.sh [seconds] --value <PROXI value> [node4021_full.py options]
#   sudo ~/car_tx.sh 1800 --value <12 hex digits> --text --dash-text "HELLO GIULIETTA"
#       (default 1800 s; Ctrl-C ends it sooner. --text shows the phone's track
#        on the radio, --text-test a fixed one, --dash-text tries the cluster)
#
# Run only with the original module unplugged: two nodes claiming 4021 would
# contend. Refuses on a quiet bus (the bench): nothing there to answer.

set -u
IF=can0
DURATION=${1:-1800}
shift $(( $# > 0 ? 1 : 0 ))
SCRIPT=${SCRIPT:-/home/admin/node4021_full.py}
LOG=/home/admin/node4021-car-$(date +%Y%m%d-%H%M%S).log

say() { echo "$(date +%T) $*"; }

flag() { ip -details link show "$IF" | grep -q "can <[^>]*$1" && echo on || echo off; }

restore() {
    ip link set "$IF" down
    ip link set "$IF" type can bitrate 50000 loopback "$lb0" listen-only "$lo0"
    ip link set "$IF" up
    [ "$app_stopped" = 1 ] && systemctl start blueandme-giulietta
    if [ "$(flag LISTEN-ONLY)" = "$lo0" ] && [ "$(flag LOOPBACK)" = "$lb0" ]; then
        say "restored as found: listen-only $lo0, loopback $lb0$([ "$app_stopped" = 1 ] && echo ', app started')"
    else
        say "NOT RESTORED (wanted listen-only $lo0, loopback $lb0), CHECK: ip -details link show $IF"
    fi
}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
[ -f "$SCRIPT" ] || { echo "missing $SCRIPT"; exit 1; }
if ! candump -n 1 -T 3000 "$IF" 2>/dev/null | grep -q .; then
    say "no frames on $IF in 3 s: not at a live bus; refusing"
    exit 1
fi

lo0=$(flag LISTEN-ONLY); lb0=$(flag LOOPBACK)
app_stopped=0
trap restore EXIT
if systemctl -q is-active blueandme-giulietta \
        && grep -qE '^  mode: (gated-)?tx( |$)' /etc/blueandme/config.yaml; then
    say "the app answers as 4021 (tx.sh on): stopped for the run"
    systemctl stop blueandme-giulietta
    app_stopped=1
fi
ip link set "$IF" down
ip link set "$IF" type can bitrate 50000 loopback off listen-only off
ip link set "$IF" up
say "$IF transmitting for up to $DURATION s; log $LOG; Ctrl-C to stop"
/opt/blueandme/.venv/bin/python "$SCRIPT" --transmit --duration "$DURATION" --log "$LOG" "$@"
