#!/bin/bash
#
# Mark the phone's state on the Pi into markers.csv, with this PC's clock (the
# same clock as fnb58_log.py's samples): music playing, call up, phone away.
#
#   experiments/standby/mark_states.sh markers.csv [pi-host]
#
# Polls every 2 s over one shared SSH connection; writes a line on each change.

MARKS=$1
HOST=${2:-blueandme}
CTL=/tmp/mark-states-ssh-$$
ssh -o ControlMaster=yes -o ControlPath=$CTL -o ControlPersist=600 -fN "$HOST"
trap 'ssh -o ControlPath=$CTL -O exit "$HOST" 2>/dev/null' EXIT

probe='
A=$(bluetoothctl devices Paired | awk "\$1 == \"Device\" {print \$2; exit}")
P=/org/bluealsa/hci0/dev_$(echo $A | tr : _)/a2dpsnk/source
c=$(bluetoothctl info $A | grep -c "Connected: yes")
m=$(busctl get-property org.bluealsa $P org.bluealsa.PCM1 Running 2>/dev/null | grep -c true)
k=$(blueandme-call calls 2>/dev/null | grep -c "^+CLCC")
echo "$c $m $k"'

last=
while true; do
    read -r conn music call < <(ssh -o ControlPath=$CTL "$HOST" "$probe" 2>/dev/null)
    if [ "${conn:-}" = 0 ]; then state="P1 phone away: link down, Pi paging every 20 s"
    elif [ "${call:-0}" -gt 0 ]; then state="W3 call up"
    elif [ "${music:-0}" -gt 0 ]; then state="W2 music playing"
    else state="W1 idle: phone connected, nothing playing, Wi-Fi on"
    fi
    if [ -n "${conn:-}" ] && [ "$state" != "$last" ]; then
        echo "$(date +%s.%N | cut -c1-14),$state" >> "$MARKS"
        echo "$(date +%T) $state"
        last=$state
    fi
    sleep 2
done
