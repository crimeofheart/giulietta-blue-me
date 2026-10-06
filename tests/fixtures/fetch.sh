#!/bin/sh
#
# Download the Doblò traces the tests compare against (see README.md here):
# the first 2000 lines of each file in Traces/ of fmntf/fiatcan, at a fixed commit.

set -eu
cd "$(dirname "$0")"
COMMIT=5a058667fa842f9d85782db24ce8e113cfa68632
BASE=https://raw.githubusercontent.com/fmntf/fiatcan/$COMMIT/Traces
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

for name in 01-radio-unit 02-driving 03-radio-play-mute-menu-esc; do
    for ext in log decoded.log; do
        curl -fsSL "$BASE/$name.$ext" -o "$TMP"
        head -n 2000 "$TMP" > "doblo-$name.$ext"
        echo "doblo-$name.$ext"
    done
done
