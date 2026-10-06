#!/bin/sh
#
# Wi-Fi off while the phone's audio runs, back on once it has stopped.
#
# The Zero W has one radio for Wi-Fi and Bluetooth. In the car, joined to the
# phone's hotspot, music lost 150-320 packets a minute ("Missing RTP packets"
# from bluealsa), 500-1200 during SSH traffic; with the Wi-Fi off for 90 s, none
# (2026-10-02, owner: "almost no drops", choppy again when it came back). The
# CPU was 45 % idle and the sound card had no underruns.
#
# WIFI_QUIET_WHEN (in /etc/blueandme/wifi-quiet.env):
#   audio      music or a call is running (a bluealsa PCM is "Running"): pause
#              the music to reach the Pi over SSH
#   connected  the phone is connected at all: switch the phone's Bluetooth off
#              to reach the Pi
#
# Only Wi-Fi this service switched off is switched on again, and never while
# the adapter is off: that is standby (power/standby.py), which switches the
# Wi-Fi itself, in both directions. A power cut with the Wi-Fi off is undone at
# boot by blueandme-wifi-on.

WHEN=${WIFI_QUIET_WHEN:-audio}
BACK_ON_AFTER=${WIFI_QUIET_BACK_ON_S:-30}
POLL=3                                 # s; a busctl spawn each, as the uplink does
FLAG=/run/blueandme-wifi-quiet.off     # we switched it off

busy() {
    case "$WHEN" in
        connected)
            hcitool con 2>/dev/null | grep -q ACL ;;
        *)
            busctl call org.bluealsa /org/bluealsa \
                org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null |
                grep -q '"Running" b true' ;;
    esac
}

bluetooth_on() {
    busctl get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered 2>/dev/null |
        grep -q true
}

echo "Wi-Fi off while: $WHEN; back on $BACK_ON_AFTER s after"
quiet=0
while :; do
    if busy; then
        quiet=0
        if [ ! -e "$FLAG" ] && [ "$(nmcli radio wifi 2>/dev/null)" = enabled ]; then
            echo "phone audio: Wi-Fi off"
            nmcli radio wifi off && : > "$FLAG"
        fi
    elif [ -e "$FLAG" ]; then
        if ! bluetooth_on; then
            echo "Bluetooth off (standby): Wi-Fi left to standby"
            rm -f "$FLAG"
        else
            quiet=$((quiet + POLL))
            if [ "$quiet" -ge "$BACK_ON_AFTER" ]; then
                echo "quiet for $quiet s: Wi-Fi on"
                rm -f "$FLAG"
                nmcli radio wifi on
            fi
        fi
    fi
    sleep "$POLL"
done
