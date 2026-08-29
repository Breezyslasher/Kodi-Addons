#!/usr/bin/env bash
# Build inputstream.adaptive 21.5.22 with the CDM call instrumented.
#
# Why: on ISA 21.5.22 this addon's audio decodes to noise and ISA reports no
# error at all -- as far as it is concerned decryption succeeded. Reading the
# source ruled out the obvious candidates (subsample handling, the 8-byte IV,
# the senc injection difference, the key source, Bento4) without finding the
# defect, so the next step is to watch what this build actually hands the CDM.
#
# It does not fix anything. It makes the failure legible, and a fix can be
# written once it is.
set -euo pipefail

ISA_TAG=${ISA_TAG:-21.5.22-Omega}
KODI_BRANCH=${KODI_BRANCH:-Omega}
WORK=${WORK:-$HOME/isa21-build}
HERE=$(cd "$(dirname "$0")" && pwd)

# The patch, wherever it landed. A browser may have saved it beside the script
# or somewhere else entirely, or renamed it; say where we looked rather than
# failing with one path the user did not choose.
find_patch() {
  local name="0001-probe-the-cdm-call.patch"
  local tried=()
  for candidate in "${PATCH:-}" "$HERE/$name" "$PWD/$name" \
                   "$HOME/Downloads/$name" "$HOME/$name"; do
    [ -n "$candidate" ] || continue
    tried+=("$candidate")
    if [ -f "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  # Last resort: anything that looks like it, near where we are.
  local found
  found=$(find "$HERE" "$PWD" "$HOME/Downloads" -maxdepth 1 \
            -name "*probe-the-cdm-call*.patch" 2>/dev/null | head -1)
  if [ -n "$found" ]; then
    printf '%s' "$found"
    return 0
  fi
  echo "Could not find $name. Looked in:" >&2
  printf '  %s\n' "${tried[@]}" >&2
  echo >&2
  echo "Point at it directly:  PATCH=/path/to/$name $0" >&2
  return 1
}

echo "==> working in $WORK"
mkdir -p "$WORK"
cd "$WORK"

[ -d xbmc ] || git clone --depth 1 --branch "$KODI_BRANCH" https://github.com/xbmc/xbmc
[ -d inputstream.adaptive ] || git clone --no-single-branch --depth 1 \
    https://github.com/xbmc/inputstream.adaptive
cd inputstream.adaptive
git fetch --depth 1 origin tag "$ISA_TAG"
git checkout -f "$ISA_TAG"
git checkout -- .
PATCH_FILE=$(find_patch)
echo "==> applying $PATCH_FILE"
git apply --check "$PATCH_FILE"
git apply "$PATCH_FILE"

cd "$WORK"
rm -rf build && mkdir build && cd build
cmake -DADDONS_TO_BUILD=inputstream.adaptive \
      -DADDON_SRC_PREFIX="$WORK" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$WORK/addon" \
      -DPACKAGE_ZIP=1 \
      "$WORK/xbmc/cmake/addons"
make -j"$(nproc)"

echo
echo "==> built. The library to install:"
find "$WORK" -name "inputstream.adaptive.so*" -newer "$WORK/build" 2>/dev/null || \
  find "$WORK" -name "inputstream.adaptive.so*"
cat <<'NOTE'

Install it over the one Kodi already has -- back the original up first:

  ADDON=~/.kodi/addons/inputstream.adaptive        # or the Flatpak path below
  cp "$ADDON"/inputstream.adaptive.so{,.orig}
  cp <the .so printed above> "$ADDON"/inputstream.adaptive.so

Then play a VOD episode and grep the log:

  grep ytv-probe ~/.kodi/temp/kodi.log

Each line says what one sample handed the CDM -- size, subsample layout,
encryption scheme, pattern, key id, IV -- and what came back. What to look
for: an audio sample whose key id is not the audio track's, a subsample
layout that does not cover the sample, an unexpected scheme or pattern, or
Decrypt returning status 0 while the output is unchanged.

FLATPAK: a library built against your distro will not load inside the Kodi
Flatpak. Build it in the SDK instead:

  flatpak install flathub org.kde.Sdk//6.7          # whatever the runtime needs
  flatpak run --devel --command=bash tv.kodi.Kodi
  # then run this script inside that shell

and install to
  ~/.var/app/tv.kodi.Kodi/data/addons/inputstream.adaptive/
NOTE
