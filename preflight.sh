#!/bin/bash
# Pre-flight check, run on the Pi before a car session:
#
#     ssh blueandme.local 'sudo ~/giulietta-blue-me/preflight.sh'
#
# Proves the whole receive chain works with no car attached -- SPI, the INT
# line, the mcp251x driver, blueandme-capture and its 11/29-bit width counter --
# by putting the MCP2515 in its internal loopback mode, sending a few frames and
# checking they come back and get recorded.
#
# Loopback keeps the controller's TX pin recessive: frames go round inside the
# chip and never reach the transceiver. The mode switch is one atomic netlink
# call each way, from the mode it finds to loopback and back. The EXIT trap puts
# back that mode (listen-only, or transmitting after `tx.sh on`) and the app
# whatever happens, including a failure halfway through.
#
# Refuses to run if it can see live bus traffic: this is a bench test.

set -u
IF=can0
LOG=/tmp/preflight.log
FAIL=0

say()  { printf '  %-44s %s\n' "$1" "$2"; }
pass() { say "$1" "ok"; }
fail() { say "$1" "FAIL  $2"; FAIL=1; }

rx() { ip -statistics link show "$IF" | awk '/RX:/{getline; print $2; exit}'; }
mode() { ip -details link show "$IF"; }
flag() { mode | grep -q "can <[^>]*$1" && echo on || echo off; }   # as `ip link set` takes it

restore() {
    ip link set "$IF" down 2>/dev/null
    ip link set "$IF" type can bitrate 50000 loopback "$lb0" listen-only "$lo0"
    ip link set "$IF" up
    [ "$app0" = active ] && systemctl start blueandme-giulietta 2>/dev/null
    echo
    if [ "$(flag LISTEN-ONLY)" = "$lo0" ] && [ "$(flag LOOPBACK)" = "$lb0" ]; then
        echo "  restored as found: listen-only $lo0, loopback $lb0, app $app0"
    else
        echo "  !!! could not restore listen-only $lo0, loopback $lb0 -- do NOT connect to the car"
        echo "  !!! reboot the Pi and check 'ip -details link show can0'"
        FAIL=1
    fi
    echo
    if [ "$FAIL" -eq 0 ]; then
        echo "PREFLIGHT PASS -- ready for the car"
    else
        echo "PREFLIGHT FAIL -- fix the lines marked FAIL before going"
    fi
    exit "$FAIL"
}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 2; }

echo "Pre-flight on $(hostname), $(date -Is)"
echo

# -- 1. the hardware came up ---------------------------------------------------
if dmesg | grep -q 'MCP2515 successfully initialized'; then
    pass "MCP2515 driver initialised"
else
    fail "MCP2515 driver initialised" "(check wiring and oscillator)"
    exit 1
fi

# -- 2. it came up the way the car needs it ------------------------------------
m=$(mode)
lo0=$(flag LISTEN-ONLY); lb0=$(flag LOOPBACK)
app0=$(systemctl is-active blueandme-giulietta)
app=$(awk '/^  mode:/{print $2; exit}' /etc/blueandme/config.yaml)
[ "$lo0" = on ] && ctl=listen-only || ctl=transmit
case "$ctl/$app" in
    listen-only/listen-only|transmit/gated-tx|transmit/tx) pass "can0 and app agree ($ctl)" ;;
    *) fail "can0 and app agree" "(can0 $ctl, app $app: tx.sh on or off)" ;;
esac
grep -q 'bitrate 50000' <<<"$m"  && pass "bitrate 50000" || fail "bitrate 50000" ""
grep -q 'ERROR-ACTIVE' <<<"$m"   && pass "controller state ERROR-ACTIVE" \
                                 || fail "controller state ERROR-ACTIVE" ""
grep -Eq 'restart-ms [1-9]' <<<"$m" && pass "rejoins after a bus-off (restart-ms)" \
                                    || fail "rejoins after a bus-off (restart-ms)" "(0: reinstall can0.service)"

# -- 3. refuse if a live bus is attached --------------------------------------
before=$(rx); sleep 2; after=$(rx)
if [ "$after" -gt "$before" ]; then
    echo; echo "  live bus traffic seen -- this is a bench test, disconnect the car"
    exit 3
fi
pass "no live bus attached"

trap restore EXIT

# -- 4. loopback round trip ---------------------------------------------------
systemctl stop blueandme-giulietta
ip link set "$IF" down
ip link set "$IF" type can bitrate 50000 listen-only off loopback on
ip link set "$IF" up
mode | grep -q LOOPBACK && pass "switched to internal loopback" \
                        || fail "switched to internal loopback" ""

rm -f "$LOG" "$LOG.meta.json" /tmp/preflight.err
/usr/local/bin/blueandme-capture --out "$LOG" --duration 8 --quiet \
    --label preflight >/dev/null 2>/tmp/preflight.err &
cap=$!

# Wait for the tool's own "recording ..." line rather than a fixed delay. Python
# takes about four seconds to start on a Zero W; frames sent before then are
# simply never seen. The same applies in the car -- see step 8.
t0=$(date +%s)
until grep -q '^recording' /tmp/preflight.err 2>/dev/null; do
    sleep 0.2
    if [ $(( $(date +%s) - t0 )) -ge 30 ]; then
        fail "capture started" "(no 'recording' line after 30 s)"; break
    fi
done
say "capture ready after" "$(( $(date +%s) - t0 )) s"
sleep 0.5

rx0=$(rx)
for id in 123 456 7FF; do cansend "$IF" "$id#DEADBEEF"; sleep 0.1; done
for id in 1ABCDEF0 00000100 1FFFFFFF; do cansend "$IF" "$id#CAFE"; sleep 0.1; done
wait "$cap"
rx1=$(rx)

got=$((rx1 - rx0))
[ "$got" -ge 6 ] && pass "controller received its 6 frames over SPI" \
                 || fail "controller received its 6 frames over SPI" "(got $got)"

if [ -f "$LOG.meta.json" ]; then
    widths=$(python3 -c "import json;print(json.load(open('$LOG.meta.json')).get('id_widths',{}))")
    grep -q "11-bit" <<<"$widths" && grep -q "29-bit" <<<"$widths" \
        && pass "capture recorded both ID widths" \
        || fail "capture recorded both ID widths" "$widths"
else
    fail "capture wrote its sidecar" "(no $LOG.meta.json)"
fi
