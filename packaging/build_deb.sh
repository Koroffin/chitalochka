#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: build_deb.sh <version> <dist-dir> <out-dir>" >&2
    exit 2
fi
version="$1"
dist_dir="$2"
out_dir="$3"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

mkdir -p "$staging/opt" "$staging/usr/bin" "$staging/usr/share/applications" \
    "$staging/usr/share/icons/hicolor/256x256/apps" "$staging/DEBIAN"
cp -a "$dist_dir" "$staging/opt/speedreader"
ln -s ../../opt/speedreader/SpeedReader "$staging/usr/bin/speedreader"
install -m 644 "$root/packaging/speedreader.desktop" "$staging/usr/share/applications/speedreader.desktop"
install -m 644 "$root/speedreader/assets/icon.png" "$staging/usr/share/icons/hicolor/256x256/apps/speedreader.png"
# mktemp -d gives 0700 and the build umask may leave group-writable dirs; both go into the package.
chmod -R u+rwX,go+rX,go-w "$staging"

installed_size="$(du -sk --exclude=DEBIAN "$staging" | cut -f1)"
cat > "$staging/DEBIAN/control" <<CONTROL
Package: speedreader
Version: $version
Section: text
Priority: optional
Architecture: amd64
Maintainer: SpeedReader <speedreader@example.invalid>
Installed-Size: $installed_size
Depends: libxcb-cursor0, libxkbcommon0, libgl1, libegl1, libfontconfig1, libdbus-1-3, libxcb-xinerama0
Description: Reader with a running word highlight for loveread.ec books
CONTROL

mkdir -p "$out_dir"
dpkg-deb --build --root-owner-group "$staging" "$out_dir/speedreader_${version}_amd64.deb"
