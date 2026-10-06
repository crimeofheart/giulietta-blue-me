#!/bin/bash
#
# Bench run of blueandme-standby itself, with short times: armed by a frame the
# MCP2515 loops back, lean after QUIET s, the hold (HOLD_H hours) running out
# (--bench: the service logs the halt instead of halting), then woken by a
# second looped-back frame. Wi-Fi goes off in the middle, so run detached:
#
#   sudo systemd-run --unit=standby-service-sim --collect \
#       ~/giulietta-blue-me/experiments/standby/service_sim.sh
#   journalctl -u standby-service-sim -u standby-test -o cat
#
# The installed blueandme-standby is stopped for the run and started again.

set -u
QUIET=${QUIET:-20}
HOLD_H=${HOLD_H:-0.05}          # 3 minutes
IF=can0
CFG=/tmp/standby-test.yaml

say() { echo "$(date +%T) $*"; }

inject() {  # one frame through internal loopback, never normal mode
    ip link set "$IF" down
    ip link set "$IF" type can bitrate 50000 listen-only off loopback on
    ip link set "$IF" up
    cansend "$IF" "123#DEADBEEF"
    sleep 0.3
    ip link set "$IF" down
    ip link set "$IF" type can bitrate 50000 loopback off listen-only on
    ip link set "$IF" up
}

restore() {
    systemctl stop standby-test 2>/dev/null
    ip -details link show "$IF" | grep -q '<LISTEN-ONLY>' || {
        ip link set "$IF" down
        ip link set "$IF" type can bitrate 50000 loopback off listen-only on
        ip link set "$IF" up
    }
    nmcli radio wifi on
    bluetoothctl power on >/dev/null
    systemctl start blueandme-bt-reconnect blueandme-standby
    say "restored (can0 $(ip -details link show "$IF" | grep -o '<LISTEN-ONLY>'))"
}

if candump -n 1 -T 3000 "$IF" 2>/dev/null | grep -q .; then
    say "frames on $IF: a live bus is attached; refusing"
    exit 1
fi
trap restore EXIT

sed -e "s/^  standby_after_quiet_s:.*/  standby_after_quiet_s: $QUIET/" \
    -e "s/^  standby_hold_h:.*/  standby_hold_h: $HOLD_H/" \
    /etc/blueandme/config.yaml > "$CFG"
grep -q "standby_after_quiet_s: $QUIET" "$CFG" || {
    # an older config without the standby keys: append them
    printf '  standby: true\n  standby_after_quiet_s: %s\n  standby_hold_h: %s\n' \
        "$QUIET" "$HOLD_H" > /tmp/standby-keys
    sed -i '/^power:/r /tmp/standby-keys' "$CFG"
}
systemctl stop blueandme-standby
systemd-run --unit=standby-test --collect \
    /opt/blueandme/.venv/bin/blueandme-standby --bench --config "$CFG"
sleep 8
say "arming: one looped-back frame"
inject
sleep $(( QUIET + 10 ))
say "should be lean now; hold runs out in $(python3 -c "print(round($HOLD_H*3600))") s"
sleep $(python3 -c "print(round($HOLD_H*3600) + 15)")
say "waking: one looped-back frame"
inject
sleep 25
say "done"
