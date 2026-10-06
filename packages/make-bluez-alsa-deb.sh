#!/usr/bin/env bash
#
# Package the bluez-alsa build that install.sh compiles (mSBC, for wideband
# calls) as a .deb, so that later installs on the same OS release install it
# in seconds instead of compiling for 10 minutes.
#
# Run on the Pi after install.sh has built it, then copy the .deb back into
# packages/ in the repo and commit it:
#
#   sudo ~/giulietta-blue-me/packages/make-bluez-alsa-deb.sh
#   scp blueandme:giulietta-blue-me/packages/*.deb packages/      # on the dev machine
#
# The package holds only the files under /usr/local. install.sh does the
# dpkg-divert swap over Debian's bluez-alsa itself, as it does for a build.

set -euo pipefail

BZA_VERSION=4.3.1
REVISION=1
BUILD=/usr/local/src/bluez-alsa-$BZA_VERSION/build

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$BUILD/Makefile" ] || { echo "no build in $BUILD; run install.sh first" >&2; exit 1; }

# In a subshell: os-release sets VERSION, among others.
CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
ARCH="$(dpkg --print-architecture)"
PKGVER="$BZA_VERSION-$REVISION~$CODENAME"
OUT="$(cd "$(dirname "$0")" && pwd)/bluez-alsa-msbc_${PKGVER}_$ARCH.deb"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root"
make -C "$BUILD" install DESTDIR="$ROOT" > "$WORK/install.log" 2>&1 ||
    { tail -n 20 "$WORK/install.log" >&2; exit 1; }
# Static and libtool archives are only for linking against the plugins, which
# nothing does. The binaries are stripped below: nothing debugs them on the Pi.
find "$ROOT" \( -name '*.la' -o -name '*.a' \) -delete

# Runtime dependencies, versioned, from the libraries the binaries link.
# dpkg-shlibdeps wants a debian/control to exist, even a bare one.
mkdir -p "$WORK/debian"
printf 'Source: bluez-alsa-msbc\n\nPackage: bluez-alsa-msbc\nArchitecture: any\n' > "$WORK/debian/control"
mapfile -t ELF < <(find "$ROOT" -type f \( -perm -u+x -o -name '*.so' \))
strip --strip-unneeded "${ELF[@]}"
DEPENDS="$(cd "$WORK" && dpkg-shlibdeps -O "${ELF[@]}" | sed -n 's/^shlibs:Depends=//p')"
[ -n "$DEPENDS" ] || { echo "dpkg-shlibdeps found no dependencies" >&2; exit 1; }

mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<EOF
Package: bluez-alsa-msbc
Version: $PKGVER
Architecture: $ARCH
Maintainer: giulietta-blue-me <root@localhost>
Section: sound
Priority: optional
Depends: $DEPENDS
Installed-Size: $(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
Description: bluez-alsa $BZA_VERSION with mSBC wideband speech, under /usr/local
 Built by giulietta-blue-me's install.sh from the upstream release tarball with
 --enable-msbc --enable-aplay --enable-rfcomm. install.sh swaps it in over
 Debian's bluez-alsa with dpkg-divert; install.sh --no-wideband swaps back.
EOF

dpkg-deb --root-owner-group --build "$ROOT" "$OUT" >/dev/null
echo "$OUT"
dpkg-deb --info "$OUT" | sed -n 's/^ \(Version\|Depends\|Installed-Size\):/  \1:/p'
