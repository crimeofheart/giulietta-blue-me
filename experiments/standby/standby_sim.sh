#!/bin/bash
#
# Standby simulation, for a USB power meter between the power bank and the Pi.
#
# Steps the Pi through ever leaner parked states, HOLD seconds each, so the
# owner can read the meter in each; then simulates the car waking with a CAN
# frame (the MCP2515's internal loopback, as preflight.sh does, never normal
# mode) and times how long until the phone has music and calls again.
#
# Wi-Fi goes off in the middle, so this runs detached and restores everything
# itself, on success, failure or kill (EXIT trap):
#
#   sudo systemd-run --unit=standby-sim --collect ~/giulietta-blue-me/experiments/standby/standby_sim.sh
#   journalctl -u standby-sim -o cat        # afterwards; also /var/tmp/standby-sim.log
#
# Bench only: it refuses to run if frames arrive on can0 (a live bus).

set -u
HOLD=${HOLD:-90}
IF=can0
LOG=/var/tmp/standby-sim.log
GOV=/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
LED=/sys/class/leds/ACT
CARD=/sys/bus/usb/devices/1-1/power

: > "$LOG"
T_START=$(date +%s)
say() { printf '%s  +%4ss  %s\n' "$(date +%T)" "$(( $(date +%s) - T_START ))" "$*" | tee -a "$LOG"; }
ms_since() { echo $(( ($(date +%s%N) - $1) / 1000000 )); }

restore() {
    ip link set "$IF" down 2>/dev/null
    ip link set "$IF" type can bitrate 50000 loopback off listen-only on
    ip link set "$IF" up
    echo ondemand > "$GOV"
    echo mmc0 > "$LED/trigger" 2>/dev/null
    echo on > "$CARD/control" 2>/dev/null
    bluetoothctl power on >/dev/null
    systemctl start blueandme-bt-reconnect
    nmcli radio wifi on
    ip -details link show "$IF" | grep -q '<LISTEN-ONLY>' \
        && say "restored: can0 listen-only, Wi-Fi, Bluetooth, CPU, LED, card" \
        || say "!!! can0 NOT listen-only -- do not connect to the car"
}

phone=$(bluetoothctl devices Paired | awk '$1 == "Device" {print $2; exit}')
pcm=/org/bluealsa/hci0/dev_$(echo "$phone" | tr : _)

# A live bus sends frames within seconds; the bench sends none.
if candump -n 1 -T 3000 "$IF" 2>/dev/null | grep -q .; then
    say "frames on $IF: a live bus is attached; refusing"
    exit 1
fi
trap restore EXIT

say "phone $phone. Each state lasts ${HOLD} s; read the meter in its last 30 s"

say "S1 parked, no paging: reconnect stopped, Bluetooth off (Wi-Fi still on)"
systemctl stop blueandme-bt-reconnect
bluetoothctl power off >/dev/null
sleep "$HOLD"

say "S2 + Wi-Fi off"
nmcli radio wifi off
sleep "$HOLD"

say "S3 + CPU powersave, ACT LED off, sound card autosuspend"
echo powersave > "$GOV"
echo none > "$LED/trigger"; echo 0 > "$LED/brightness"
echo 2000 > "$CARD/autosuspend_delay_ms"; echo auto > "$CARD/control"
sleep 5
say "  sound card runtime status: $(cat "$CARD/runtime_status")"
sleep $(( HOLD - 5 ))

say "WAKE: simulated car wake, one CAN frame through internal loopback"
ip link set "$IF" down
ip link set "$IF" type can bitrate 50000 listen-only off loopback on
ip link set "$IF" up
candump -n 1 -T 5000 "$IF" > /tmp/standby-wake-frame &
watcher=$!
sleep 0.5
T0=$(date +%s%N)
cansend "$IF" "123#DEADBEEF"
wait "$watcher"
say "  frame seen after $(ms_since "$T0") ms: $(cat /tmp/standby-wake-frame)"
ip link set "$IF" down
ip link set "$IF" type can bitrate 50000 loopback off listen-only on
ip link set "$IF" up

# Resume: what a key-on would do. Wi-Fi last, so it does not compete.
echo ondemand > "$GOV"
echo on > "$CARD/control"
echo mmc0 > "$LED/trigger"
bluetoothctl power on >/dev/null
say "  Bluetooth on after $(ms_since "$T0") ms; paging"
/usr/local/libexec/blueandme-bt-firstpage | while read -r line; do say "  firstpage: $line"; done &
music= calls=
while [ "$(ms_since "$T0")" -lt 40000 ]; do
    objs=$(busctl call org.bluealsa /org/bluealsa org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null)
    [ -z "$music" ] && case "$objs" in *"$pcm/a2dp"*) music=$(ms_since "$T0"); say "  music after $music ms";; esac
    [ -z "$calls" ] && case "$objs" in *"$pcm/hfp"*) calls=$(ms_since "$T0"); say "  calls after $calls ms";; esac
    [ -n "$music" ] && [ -n "$calls" ] && break
    sleep 0.2
done
wait
say "RESULT wake -> music ${music:-never} ms, calls ${calls:-never} ms"
# restore (trap) turns Wi-Fi and the reconnect service back on
