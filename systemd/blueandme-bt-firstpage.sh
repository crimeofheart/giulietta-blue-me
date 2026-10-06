#!/bin/sh
#
# Page the paired phone at boot, as soon as bluealsa has registered music and
# calls with BlueZ. blueandme-bt-reconnect does the same and more (checks both
# profiles came up, retries, backs off), but it is Python, and under boot load
# Python took 8.5 s just to start (2026-09-25). This is the first page only;
# the reconnect service takes over when it exits.
#
# The page asks for the profiles by name (Device1.ConnectProfile), music then
# calls. A plain connect (bluetoothctl connect, Device1.Connect) that the Pi
# opens brought up the link with neither, for 40 s and more, and Android does
# not make a device without them its audio device. By name, both were up in
# 2.1 s (2026-09-25, Pi's Bluetooth off for 40 s first, as after a reboot).

AUDIO_SINK=0000110b-0000-1000-8000-00805f9b34fb   # ours, registered by bluealsa
HANDSFREE=0000111e-0000-1000-8000-00805f9b34fb    # ours, registered by bluealsa
PHONE_A2DP=0000110a-0000-1000-8000-00805f9b34fb   # the phone's Audio Source
PHONE_HFP=0000111f-0000-1000-8000-00805f9b34fb    # the phone's Handsfree Audio Gateway

ready=
i=0
while [ $i -lt 40 ]; do            # up to 20 s
    show=$(bluetoothctl show 2>/dev/null)
    case "$show" in *"$AUDIO_SINK"*)
        case "$show" in *"$HANDSFREE"*) ready=1; break ;; esac ;;
    esac
    sleep 0.5
    i=$((i + 1))
done
if [ -z "$ready" ]; then
    echo "bluealsa not registered after 20 s; leaving it to blueandme-bt-reconnect"
    exit 0
fi

profile() {  # profile <device path> <uuid>
    busctl call --timeout=20 org.bluez "$1" org.bluez.Device1 ConnectProfile s "$2" 2>&1
}

for dev in $(bluetoothctl devices Paired 2>/dev/null | awk '$1 == "Device" {print $2}'); do
    info=$(bluetoothctl info "$dev" 2>/dev/null)
    case "$info" in *"Trusted: yes"*) ;; *) continue ;; esac
    case "$info" in *"Connected: yes"*) echo "$dev already connected"; exit 0 ;; esac
    path=/org/bluez/hci0/dev_$(echo "$dev" | tr : _)
    echo "paging $dev"
    if out=$(profile "$path" "$PHONE_A2DP"); then
        echo "music connected"
    else
        echo "music: ${out:-failed}"
        # Not there at all: do not spend a second page timeout on calls.
        bluetoothctl info "$dev" 2>/dev/null | grep -q "Connected: yes" || continue
    fi
    # Straight after music, BlueZ is often still busy with the link
    # (br-connection-busy), and the phone sometimes brings calls up itself.
    pcm=/org/bluealsa/hci0/dev_$(echo "$dev" | tr : _)/hfp
    n=0
    while [ $n -lt 5 ]; do
        case "$(busctl call org.bluealsa /org/bluealsa \
                org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null)" in
            *"$pcm"*) echo "calls connected"; exit 0 ;;
        esac
        out=$(profile "$path" "$PHONE_HFP") && { echo "calls connected"; exit 0; }
        case "$out" in *busy*) sleep 1 ;; *) echo "calls: ${out:-failed}"; exit 0 ;; esac
        n=$((n + 1))
    done
    echo "calls: still busy; leaving it to blueandme-bt-reconnect"
    exit 0
done
exit 0
