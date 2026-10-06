#!/bin/bash
#
# Switch the installed app between listen-only and answering the car as
# node 4021 (step 12, through TxGate), in one step each way:
#
#   sudo ~/giulietta-blue-me/tx.sh on        can0 normal mode, app gated-tx, node 4021's six frames
#   sudo ~/giulietta-blue-me/tx.sh on bm_proxi_response bm_status_response   only these
#   sudo ~/giulietta-blue-me/tx.sh off       both back to listen-only
#   sudo ~/giulietta-blue-me/tx.sh status
#
# Only with the original Blue&Me module unplugged: two nodes claiming 4021
# would contend. Not while experiments/node4021/car_tx.sh runs: that is the
# other way of doing the same thing.

set -u
ENV=/etc/blueandme/can0.env
CFG=/etc/blueandme/config.yaml
NODE_FRAMES=(bm_status_response bm_proxi_response bm_watchdog bm_track_time bm_audio_channel
             bm_text_message)

status() {
    echo "can0:  $(ip -details link show can0 2>/dev/null | grep -o '<LISTEN-ONLY>' || echo 'normal mode (can transmit)')"
    echo "app:   $(grep -E '^  mode:' "$CFG" | sed 's/^ *//')"
    echo "       $(grep -E '^  allowed_tx_frames:' "$CFG" | sed 's/^ *//')"
    journalctl -u blueandme-giulietta -n 30 -o cat --no-pager 2>/dev/null \
        | grep -E "opened can0|not sending|REFUSED" | tail -3 | sed 's/^/       /'
}

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
case "${1:-status}" in
    on)
        shift
        frames=("$@")
        [ ${#frames[@]} -gt 0 ] || frames=("${NODE_FRAMES[@]}")
        if pgrep -f "node4021_ful[l].py" >/dev/null; then
            echo "car_tx.sh / node4021_full.py is running: stop it first"; exit 1
        fi
        list=$(IFS=,; echo "${frames[*]}" | sed 's/,/, /g')
        sed -i 's/^CTRLMODE=.*/CTRLMODE=listen-only off/' "$ENV"
        sed -i -e 's/^  mode:.*/  mode: gated-tx/' \
               -e "s/^  allowed_tx_frames:.*/  allowed_tx_frames: [$list]/" "$CFG"
        /opt/blueandme/.venv/bin/blueandme-giulietta --config "$CFG" --check || {
            echo "config refused: going back to listen-only"; exec "$0" off; }
        ;;
    off)
        sed -i 's/^CTRLMODE=.*/CTRLMODE=listen-only on/' "$ENV"
        sed -i -e 's/^  mode:.*/  mode: listen-only/' \
               -e 's/^  allowed_tx_frames:.*/  allowed_tx_frames: []/' "$CFG"
        ;;
    status) status; exit 0 ;;
    *) sed -n '3,12p' "$0"; exit 1 ;;
esac
systemctl restart can0 blueandme-giulietta
sleep 8   # the app's Python start on the Zero W
status
