#!/usr/bin/env bash
#
# Install the Blue&Me replacement on a Raspberry Pi Zero W running
# Raspberry Pi OS Lite (Bookworm, armhf), headless.
#
# Safe by construction: the installed configuration is listen-only, nothing is
# on the transmit allow-list, and the CAN interface is brought up in the
# controller's listen-only mode. A freshly installed system wired to a live car
# receives and decodes; it cannot transmit. Enabling transmission is a
# deliberate step (see README, "From listening to transmitting").
#
# Usage:  sudo ./install.sh [--oscillator 8000000|16000000] [--no-audio]
#                           [--no-wideband] [--dry-run]

set -euo pipefail

PREFIX=/opt/blueandme
CONFDIR=/etc/blueandme
STATEDIR=/var/lib/blueandme
OSCILLATOR=8000000
INT_GPIO=25
WITH_AUDIO=1
WIDEBAND=1
DRY_RUN=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "  would run: $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        --oscillator) OSCILLATOR="$2"; shift 2 ;;
        --int-gpio)   INT_GPIO="$2";   shift 2 ;;
        --no-audio)   WITH_AUDIO=0;    shift ;;
        --no-wideband) WIDEBAND=0;     shift ;;
        --dry-run)    DRY_RUN=1;       shift ;;
        -h|--help)    sed -n '2,13p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ "$(id -u)" = 0 ] || die "run as root (sudo ./install.sh)"

case "$OSCILLATOR" in
    8000000|16000000) ;;
    *) die "--oscillator must be 8000000 or 16000000, matching the crystal
       marking on your MCP2515 module. Getting this wrong silently halves or
       doubles the effective bitrate and the bus will look dead." ;;
esac

SRC="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------- packages ---
PACKAGES=(python3 python3-venv python3-pip python3-dbus python3-gi can-utils
          bluez bluez-tools bluez-alsa-utils libasound2-plugin-bluez alsa-utils)
# A re-install on a working Pi skips apt: its index refresh alone takes minutes
# on the Zero W.
if dpkg-query -W -f='${Status}\n' "${PACKAGES[@]}" 2>/dev/null \
        | grep -qv "install ok installed" \
        || [ "$(dpkg-query -W -f='${Status}\n' "${PACKAGES[@]}" 2>/dev/null | wc -l)" -ne ${#PACKAGES[@]} ]; then
    log "installing packages"
    run apt-get update
    run apt-get install -y --no-install-recommends "${PACKAGES[@]}"
else
    log "packages already installed"
fi

# python3-dbus and python3-gi come from apt rather than pip: building them from
# source on a single-core ARM11 takes the better part of an hour.

# ------------------------------------------------------------------ python ---
log "installing the application into $PREFIX"
run install -d -m 0755 "$PREFIX" "$CONFDIR" "$STATEDIR" "$STATEDIR/captures"
run cp -r "$SRC/blueandme" "$SRC/pyproject.toml" "$SRC/README.md" "$PREFIX/"
[ -d "$SRC/docs" ] && run cp -r "$SRC/docs" "$PREFIX/"

# Retry the network once, not five times: piwheels.org has answered with a
# TLS error, and pip's backoff then cost ~8 minutes per install (2026-09-26).
PIP=("$PREFIX/.venv/bin/pip" --disable-pip-version-check --retries 1 --timeout 20)
if [ ! -d "$PREFIX/.venv" ]; then
    # --system-site-packages so the venv can see apt's dbus and gi bindings.
    run python3 -m venv --system-site-packages "$PREFIX/.venv"
    run "${PIP[@]}" install --upgrade pip
    run "${PIP[@]}" install "$PREFIX"
else
    # Re-install: build with the venv's own setuptools rather than download
    # a fresh one into an isolated build environment every time.
    run "${PIP[@]}" install --no-build-isolation "$PREFIX" \
        || run "${PIP[@]}" install "$PREFIX"
fi

log "linking commands into /usr/local/bin"
for cmd in blueandme-giulietta blueandme-capture blueandme-decode \
           blueandme-diff blueandme-status blueandme-protocol-map \
           blueandme-sco-uplink blueandme-bt-reconnect blueandme-pair \
           blueandme-call blueandme-standby; do
    run ln -sf "$PREFIX/.venv/bin/$cmd" "/usr/local/bin/$cmd"
done

# ------------------------------------------------------------------ config ---
if [ -f "$CONFDIR/config.yaml" ]; then
    log "keeping existing $CONFDIR/config.yaml"
else
    log "installing default config (listen-only)"
    run cp "$SRC/blueandme/config/default.yaml" "$CONFDIR/config.yaml"
fi
[ -f "$CONFDIR/can0.env" ] || run cp "$SRC/systemd/can0.env" "$CONFDIR/can0.env"

# --------------------------------------------------------------- boot config ---
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
[ -f "$BOOTCFG" ] || die "cannot find config.txt"

add_line() {
    if grep -qxF "$1" "$BOOTCFG"; then
        echo "  already present: $1"
    else
        log "adding to $BOOTCFG: $1"
        run sh -c "printf '%s\n' \"$1\" >> '$BOOTCFG'"
        NEEDS_REBOOT=1
    fi
}

remove_line() {
    if grep -qxF "$1" "$BOOTCFG"; then
        log "removing from $BOOTCFG: $1"
        # cat > rather than mv: config.txt lives on a FAT partition.
        run sh -c "grep -vxF \"$1\" '$BOOTCFG' > '$BOOTCFG.new' && cat '$BOOTCFG.new' > '$BOOTCFG' && rm '$BOOTCFG.new'"
        NEEDS_REBOOT=1
    fi
}

NEEDS_REBOOT=0
log "configuring SPI and the MCP2515"
add_line "dtparam=spi=on"
add_line "dtoverlay=mcp2515-can0,oscillator=${OSCILLATOR},interrupt=${INT_GPIO}"
# Deliberately NOT adding spi0-0cs / spi0-1cs: those reduce the number of
# hardware chip selects, and the mcp2515-can0 overlay needs CE0 (GPIO8).

# ---------------------------------------------------------------- boot time ---
# The Zero W has one core, and everything that starts at boot queues for it.
# Measured 2026-09-24 with the stock image: udev reached the Bluetooth chip 18 s
# after its firmware loaded, and the phone was paged 66 s after power-on,
# although the page itself took 2 s. A car unit has no display, camera, modem,
# keyboard or disks, so none of their setup runs.
log "trimming boot for a headless car unit"
# No initramfs: ext4 and the SD card driver are built into the Pi kernel, and
# unpacking the initramfs and running its /init took ~9 s before root mounted.
remove_line "auto_initramfs=1"
# No KMS display driver (3.5 s loading drm) and no camera or display probing.
remove_line "dtoverlay=vc4-kms-v3d"
remove_line "camera_auto_detect=1"
remove_line "display_auto_detect=1"
add_line "disable_splash=1"
add_line "boot_delay=0"
# quiet: the kernel stops drawing its log on the console as it boots.
CMDLINE="$(dirname "$BOOTCFG")/cmdline.txt"
if [ -f "$CMDLINE" ] && ! grep -qw quiet "$CMDLINE"; then
    log "adding quiet to $CMDLINE"
    # One line, edited in place with cat > for the FAT partition.
    run sh -c "sed '1 s/\$/ quiet/' '$CMDLINE' > '$CMDLINE.new' && cat '$CMDLINE.new' > '$CMDLINE' && rm '$CMDLINE.new'"
    NEEDS_REBOOT=1
fi
# udev loads drivers device by device, and on this core it reached the
# Bluetooth UART at 30 s and the MCP2515 at 41 s. Loaded by name at the start
# of boot (~8 s) instead; the SPI controller first, so the MCP2515 is found.
# The sound card's driver too, and not only for time: the players run with
# DeviceAllow=char-alsa, which systemd resolves when they start. Started before
# the sound core exists (22 s, driver at 50 s, 2026-09-25), they can never open
# the card, and music and calls are silent until they are restarted.
run sh -c 'cat > /etc/modules-load.d/blueandme.conf <<EOF
# Written by the Blue&Me replacement install.sh: the CAN, Bluetooth and sound
# card drivers at the start of boot, not when udev gets to them.
spi_bcm2835
mcp251x
hci_uart
snd_usb_audio
EOF'
# With the chip up that early, bluetoothd starts at ~22 s, but udev still only
# reaches the chip at ~44 s. Until then the radio stays rfkill-blocked (the Pi
# image starts every radio blocked, and systemd-rfkill waits for udev before
# restoring it), so bluetoothd's power-on failed and was never retried. So the
# drop-in unblocks Bluetooth itself, and starts bluetooth.target (bluealsa and
# the call and reconnect units) from bluetoothd instead of from udev.
run install -d -m 0755 /etc/systemd/system/bluetooth.service.d
run sh -c "cat > /etc/systemd/system/bluetooth.service.d/blueandme-early.conf <<'EOF'
# Written by the Blue&Me replacement install.sh: see install.sh, boot time.
[Unit]
Wants=bluetooth.target
[Service]
ExecStartPre=+/bin/sh -c 'for r in /sys/class/rfkill/rfkill*; do [ \"\$\$(cat \$\$r/type)\" = bluetooth ] && echo 0 > \$\$r/soft; done; true'
EOF"
# The camera and video codec drivers cost ~4 s of that udev time, for nothing.
run sh -c 'cat > /etc/modprobe.d/blueandme-no-camera.conf <<EOF
# Written by the Blue&Me replacement install.sh: no camera, no video codecs.
blacklist bcm2835_codec
blacklist bcm2835_isp
blacklist bcm2835_v4l2
blacklist bcm2835_mmal_vchiq
blacklist vc_sm_cma
EOF'
# Units with nothing to do here, each costing CPU at boot:
#   ModemManager, udisks2        no modem, no disks to automount
#   triggerhappy                 no hotkeys
#   keyboard-setup, console-setup  no keyboard or local console
#   rpi-eeprom-update            Pi 4 and 5 only
#   rpi-display-backlight        no display
#   e2scrub_reap, e2scrub_all    LVM snapshots only
#   NetworkManager-wait-online, nfs-client  nothing waits for the network
# and timers that would hold the core for minutes, in the middle of a call:
#   apt-daily, apt-daily-upgrade, man-db    (install.sh runs apt itself)
# and the swap file (2.8 s at boot; ~110 of 427 MB used, and SD card wear).
# Time sync is started a minute after boot by a timer instead (below).
# Undo any one with: sudo systemctl enable <unit>
for unit in ModemManager.service udisks2.service \
            triggerhappy.service triggerhappy.socket \
            keyboard-setup.service console-setup.service \
            rpi-eeprom-update.service rpi-display-backlight.service \
            e2scrub_reap.service e2scrub_all.timer \
            NetworkManager-wait-online.service nfs-client.target \
            apt-daily.timer apt-daily-upgrade.timer man-db.timer \
            dphys-swapfile.service systemd-timesyncd.service; do
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null)" = enabled ]; then
        run systemctl disable --quiet "$unit"
    fi
done
# logind loads the display core (drm) at boot, 2.8 s here, for no display.
run systemctl mask --quiet modprobe@drm.service
# systemd-timesyncd is ordered before sysinit.target, so everything waited
# for it (2.1 s). There is no network in the car anyway.
run sh -c 'cat > /etc/systemd/system/blueandme-timesync.timer <<EOF
# Written by the Blue&Me replacement install.sh: time sync, after boot.
[Unit]
Description=Start network time sync a minute after boot, not during it

[Timer]
OnBootSec=60
Unit=systemd-timesyncd.service

[Install]
WantedBy=timers.target
EOF'
run systemctl daemon-reload
run systemctl enable --quiet blueandme-timesync.timer
# /boot/firmware is read only by install.sh and kernel upgrades, but mounting
# it at boot held local-fs.target, and so dbus and bluetoothd, until udev had
# found the partition and fsck had checked it: 7.7 -> 16.0 s. Mounted on first
# access instead.
if grep -qE '^\S+\s+/boot/firmware\s+vfat\s+defaults\s' /etc/fstab; then
    log "mounting /boot/firmware on first access"
    run sed -i -E 's#^(\S+\s+/boot/firmware\s+vfat\s+)defaults(\s)#\1defaults,noauto,x-systemd.automount\2#' /etc/fstab
fi
# One core: at boot the Bluetooth path (bluetoothd, bluealsa, the reconnect
# service) goes first, the network (only for SSH) after.
for unit in bluetooth bluealsa NetworkManager wpa_supplicant avahi-daemon; do
    case "$unit" in bluetooth|bluealsa) nice=-5 ;; *) nice=5 ;; esac
    run install -d -m 0755 "/etc/systemd/system/$unit.service.d"
    run sh -c "printf '%s\n' '# Written by the Blue&Me replacement install.sh: boot priority.' \
        '[Service]' 'Nice=$nice' > /etc/systemd/system/$unit.service.d/blueandme-priority.conf"
done

if [ "$WITH_AUDIO" = 1 ]; then
    log "configuring audio for the USB sound card"
    # The Zero W's onboard PWM audio would otherwise claim a card slot.
    add_line "dtparam=audio=off"
    # Its driver still loads at boot, making no card; skip loading it at all.
    run sh -c 'echo "blacklist snd_bcm2835  # install.sh: the USB card plays everything" > /etc/modprobe.d/blueandme-no-onboard-audio.conf'
    # Installs before 2026-09-24 added this overlay for an I2S DAC that the
    # build no longer uses. It creates a phantom card with or without a board.
    remove_line "dtoverlay=hifiberry-dac"
    # The card is a Ugreen USB adapter on a C-Media HS-100B (0d8c:0014). ALSA
    # names it "Device" after its product string. Making it the default by name
    # means bluealsa-aplay and the test tools reach it without being told.
    # Not "defaults.pcm.card Device": that key takes only a number, and ALSA
    # rejects the whole file ("card is not a string"). sysdefault:CARD= takes the
    # name, and is the card's own pipeline with mixing and resampling.
    run sh -c 'cat > /etc/asound.conf <<EOF
# Written by the Blue&Me replacement install.sh: the USB sound card by name.
# sysdefault is the default pipeline of the card itself (mixing, resampling).
pcm.!default "sysdefault:CARD=Device"
ctl.!default "sysdefault:CARD=Device"
EOF'
    # Whenever the card appears: mic auto gain off (calls need fixed gain for
    # echo cancellation) and mic loopback to the output off.
    run install -m 0644 "$SRC/udev/95-blueandme-soundcard.rules" /etc/udev/rules.d/
    run udevadm control --reload
    run udevadm trigger --action=add --subsystem-match=sound
fi

# ------------------------------------------------------------------ systemd ---
log "installing systemd units"
run cp "$SRC/systemd/can0.service" "$SRC/systemd/blueandme-giulietta.service" \
       "$SRC/systemd/blueandme-standby.service" "$SRC/systemd/blueandme-wifi-on.service" \
       /etc/systemd/system/
run systemctl daemon-reload
# Standby while parked is armed only once a car has been seen on the bus or the
# ignition, so on the bench it stays idle (power/standby.py). It switches the
# Wi-Fi off, which outlives a power cut: blueandme-wifi-on undoes that at boot.
run systemctl enable can0.service blueandme-giulietta.service blueandme-standby.service \
                     blueandme-wifi-on.service

# ---------------------------------------------------------------- bluetooth ---
log "configuring BlueZ"
if [ -f /etc/bluetooth/main.conf ]; then
    run sed -i 's/^#\?Class *=.*/Class = 0x200420/' /etc/bluetooth/main.conf
    # 0x200420: Audio/Video major class, Car Audio minor class. Phones offer
    # A2DP to a device that identifies itself as car audio without prompting.
fi
run systemctl enable bluetooth.service

# bluez-alsa-utils ships two units: the bluealsa daemon (which registers the
# A2DP endpoint with BlueZ) and bluealsa-aplay (which pumps the decoded stream
# into ALSA). Both are needed. The daemon defaults vary between releases, so
# pin the profiles we actually want rather than inheriting whatever is shipped:
# a music sink, and the hands-free side of calls (bluealsa's own hfp-hf, no
# oFono). Proven on the bench 2026-09-24.
#
# Wideband calls: Debian's bluez-alsa 4.0.0 has no mSBC, so calls run at 8 kHz.
# v4.3.1 built with --enable-msbc gives 16 kHz. On the bench (2026-09-24) the
# phone chose mSBC on the onboard chip even though the kernel does not flag it
# wide-band-speech: ~3 bad packets per 75 s, all concealed, and quiet words
# that 8 kHz lost came through. It goes into /usr/local, from the prebuilt
# package in packages/ when one matches this OS release and architecture,
# else built from the release tarball (about 10 minutes on the Zero W; turn
# that build into a package with packages/make-bluez-alsa-deb.sh). Then it is
# swapped in with dpkg-divert: the Debian packages stay installed for their
# units and D-Bus policy, and an upgrade of them lands beside the swap instead
# of over it. --no-wideband swaps Debian's files back; /usr/local stays.
BZA_VERSION=4.3.1
BZA_SHA256=933fe898dfac21fdfeb5f4ffa685c2aa2db9c064d639170ac2652f156e956a2a
BZA_SRC=/usr/local/src/bluez-alsa-$BZA_VERSION
BZA_DEB="$SRC/packages/bluez-alsa-msbc_$BZA_VERSION-1~$(. /etc/os-release &&
    echo "$VERSION_CODENAME")_$(dpkg --print-architecture).deb"
ALSALIB="$(ls -d /usr/lib/*-linux-gnu*/alsa-lib 2>/dev/null | head -n 1)" ||
    die "no ALSA plugin directory under /usr/lib (is libasound2 installed?)"
# Debian's file, then the one from the build that replaces it.
BZA_SWAP="/usr/bin/bluealsa /usr/local/bin/bluealsa
/usr/bin/bluealsa-aplay /usr/local/bin/bluealsa-aplay
$ALSALIB/libasound_module_pcm_bluealsa.so /usr/local/lib/alsa-lib/libasound_module_pcm_bluealsa.so
$ALSALIB/libasound_module_ctl_bluealsa.so /usr/local/lib/alsa-lib/libasound_module_ctl_bluealsa.so
/etc/alsa/conf.d/20-bluealsa.conf /usr/local/etc/alsa/conf.d/20-bluealsa.conf"

wideband_built() {
    [ -x /usr/local/bin/bluealsa ] &&
        [ "$(/usr/local/bin/bluealsa --version 2>&1)" = "v$BZA_VERSION" ] &&
        /usr/local/bin/bluealsa --help 2>&1 | grep -q mSBC
}

build_wideband() {
    log "building bluez-alsa $BZA_VERSION with mSBC (about 10 minutes, once)"
    run apt-get install -y --no-install-recommends \
        build-essential autoconf automake libtool pkg-config curl ca-certificates \
        libasound2-dev libbluetooth-dev libdbus-1-dev libglib2.0-dev libsbc-dev \
        libspandsp-dev libreadline-dev libbsd-dev
    # spandsp is mSBC's packet loss concealment; readline and bsd are for
    # bluealsa-rfcomm, the AT console that is handy when debugging calls.
    run rm -rf "$BZA_SRC"
    run install -d -m 0755 "$BZA_SRC"
    run curl -fsSL -o "$BZA_SRC.tar.gz" \
        "https://github.com/arkq/bluez-alsa/archive/refs/tags/v$BZA_VERSION.tar.gz"
    if [ "$DRY_RUN" = 0 ]; then
        echo "$BZA_SHA256  $BZA_SRC.tar.gz" | sha256sum -c --quiet ||
            die "bluez-alsa tarball checksum mismatch: $BZA_SRC.tar.gz"
    fi
    run tar xzf "$BZA_SRC.tar.gz" -C "$BZA_SRC" --strip-components=1
    # One log for the whole build; on failure it ends with the reason.
    run sh -c "exec > '$BZA_SRC.log' 2>&1; cd '$BZA_SRC' &&
        autoreconf --install && mkdir build && cd build &&
        ../configure --prefix=/usr/local --enable-msbc --enable-aplay \
            --enable-rfcomm --disable-manpages \
            --with-alsaplugindir=/usr/local/lib/alsa-lib \
            --with-alsaconfdir=/usr/local/etc/alsa/conf.d \
            --with-dbusconfdir=/usr/local/share/dbus-1/system.d &&
        make -j1 && make install" ||
        die "bluez-alsa build failed, last lines of $BZA_SRC.log:
$(tail -n 20 "$BZA_SRC.log")"
    [ "$DRY_RUN" = 1 ] || wideband_built ||
        die "built bluealsa is not v$BZA_VERSION with mSBC, see $BZA_SRC.log"
}

diverted() { dpkg-divert --list "$1" | grep -q .; }

if [ "$WIDEBAND" = 1 ]; then
    if wideband_built; then
        :
    elif [ -f "$BZA_DEB" ]; then
        log "installing prebuilt bluez-alsa $BZA_VERSION with mSBC"
        # apt, not dpkg -i, so the package's library dependencies come along.
        run apt-get install -y --no-install-recommends "$BZA_DEB"
        [ "$DRY_RUN" = 1 ] || wideband_built ||
            die "$BZA_DEB did not install a working bluealsa v$BZA_VERSION with mSBC"
    else
        build_wideband
    fi
    log "swapping in bluez-alsa $BZA_VERSION (Debian's files kept as *.debian)"
    echo "$BZA_SWAP" | while read -r deb ours; do
        diverted "$deb" || run dpkg-divert --divert "$deb.debian" --rename --add "$deb"
        run ln -sfn "$ours" "$deb"
    done
    # Hold a Bluetooth audio link 5 s after its last client closes, instead of
    # dropping it at once (which sends a call back to the handset). Debian's
    # 4.0 accepts the option too, but it was only tested with this build.
    BLUEALSA_OPTS="-S --keep-alive=5"
else
    echo "$BZA_SWAP" | while read -r deb ours; do
        if diverted "$deb"; then
            log "restoring Debian's $deb"
            run rm -f "$deb"
            run dpkg-divert --rename --remove "$deb"
        fi
    done
    BLUEALSA_OPTS="-S"
fi

log "configuring BlueALSA for music and hands-free calls"
run install -d -m 0755 /etc/systemd/system/bluealsa.service.d
# Installs before 2026-09-24 wrote a2dp-sink.conf, music only.
run rm -f /etc/systemd/system/bluealsa.service.d/a2dp-sink.conf
run sh -c "cat > /etc/systemd/system/bluealsa.service.d/profiles.conf <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa $BLUEALSA_OPTS -p a2dp-sink -p hfp-hf
EOF"
# Calls need three more units, each explained in its own file:
#   blueandme-sco-route   route the chip's call audio over HCI, at every boot
#   blueandme-sco-aplay   a second player, for call audio only
#   blueandme-sco-uplink  the mic to the phone while a call is up
# and two to keep the phone connected:
#   blueandme-bt-firstpage  page it at boot the moment bluealsa is ready
#   blueandme-bt-reconnect  then keep it connected with music and calls
run install -D -m 0755 "$SRC/systemd/blueandme-bt-firstpage.sh" \
    /usr/local/libexec/blueandme-bt-firstpage
run cp "$SRC/systemd/blueandme-bt-firstpage.service" \
       "$SRC/systemd/blueandme-sco-route.service" \
       "$SRC/systemd/blueandme-sco-aplay.service" \
       "$SRC/systemd/blueandme-sco-uplink.service" \
       "$SRC/systemd/blueandme-bt-reconnect.service" /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable bluealsa.service bluealsa-aplay.service \
    blueandme-sco-route.service blueandme-sco-aplay.service blueandme-sco-uplink.service \
    blueandme-bt-firstpage.service blueandme-bt-reconnect.service
# One radio for Wi-Fi and Bluetooth: Wi-Fi traffic drops the phone's audio
# packets (car, 2026-10-02). Wi-Fi goes off while music or a call runs.
run install -D -m 0755 "$SRC/systemd/blueandme-wifi-quiet.sh" \
    /usr/local/libexec/blueandme-wifi-quiet
run cp "$SRC/systemd/blueandme-wifi-quiet.service" /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable blueandme-wifi-quiet.service

# --------------------------------------------------------------------- done ---
log "verifying"
if [ "$DRY_RUN" = 0 ]; then
    "$PREFIX/.venv/bin/blueandme-giulietta" --config "$CONFDIR/config.yaml" --check
fi

cat <<'EOM'

Installed. Current state:

  CAN mode        listen-only  (software gate AND controller mode)
  profile         giulietta940
  transmittable   nothing

That is correct for a fresh install: it listens, and sends nothing until
tx.sh on. Record your own car first (README.md).

Next steps:

  1. reboot                      (required if config.txt was changed)
  2. blueandme-status            confirm can0 is up at 50000 with zero errors
  3. Check the hardware before touching the car:
       - MCP2515 crystal marking matches --oscillator
       - MISO level shifting (the board is 5 V, GPIO9 is not 5 V tolerant)
       - bus termination (measure the car's bus before adding a third 120R)
  4. blueandme-capture --scenario A --out /var/lib/blueandme/captures/A.log
  5. README.md, "From listening to transmitting"

To emergency-disable transmission at any time:

    sudo sed -i 's/^  mode:.*/  mode: listen-only/' /etc/blueandme/config.yaml
    sudo systemctl restart blueandme-giulietta

EOM
if [ "$NEEDS_REBOOT" = 1 ]; then
    warn "config.txt changed -- reboot before the CAN interface will appear"
fi
