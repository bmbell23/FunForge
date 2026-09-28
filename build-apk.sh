#!/bin/bash
# Build the FunForge debug APK and stage it where the running container serves it,
# so the phone can install (or self-update to) the new build.
#
# Usage:  ./build-apk.sh           # build + stage
#         ./build-apk.sh --clean   # gradle clean first
#
# The APK lands in data/, which is bind-mounted into the container and exposed at
# /download/. The app polls /download/version.json on launch and on resume, and
# offers to install anything newer.
set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
ANDROID_DIR="$REPO_ROOT/android"
DATA_DIR="$REPO_ROOT/data"
PORT=8006

# --- versionCode ------------------------------------------------------------
# Derived from version.txt so semver is the single source of truth:
#   1.2.1 -> 10201  (major*10000 + minor*100 + patch)
#
# The self-updater only prompts when serverCode > installedCode, so a versionCode
# that ever goes BACKWARDS silently disables updates forever. Taking the greater
# of the derived code and one past whatever is already published makes the
# sequence monotonic by construction, which self-heals a botched version.txt edit.
VERSION_NAME="$(cat "$REPO_ROOT/version.txt" 2>/dev/null || echo 0.0.0)"
VERSION_NAME="${VERSION_NAME//[[:space:]]/}"

IFS='.' read -r MAJOR MINOR PATCH <<< "$VERSION_NAME"
DERIVED=$(( ${MAJOR:-0} * 10000 + ${MINOR:-0} * 100 + ${PATCH:-0} ))

PUBLISHED=$(sed -n 's/.*"versionCode"[[:space:]]*:[[:space:]]*\([0-9]\+\).*/\1/p' \
    "$DATA_DIR/version.json" 2>/dev/null | head -1)
PUBLISHED=${PUBLISHED:-0}

if [ "$DERIVED" -gt "$PUBLISHED" ]; then
    VERSION_CODE=$DERIVED
else
    # Rebuilding without bumping version.txt (or after a botched edit). Publish
    # one past what's out there so the phone still sees this build as newer —
    # the versionName just stops matching the code until version.txt catches up.
    VERSION_CODE=$((PUBLISHED + 1))
    echo "ℹ️  version.txt ($VERSION_NAME -> $DERIVED) is at or behind the published"
    echo "   versionCode ($PUBLISHED). Publishing as $VERSION_CODE so the update"
    echo "   still reaches the phone. Bump version.txt to keep the name in step."
fi
export VERSION_CODE VERSION_NAME

echo "🔨 Building FunForge debug APK (version $VERSION_NAME, versionCode $VERSION_CODE)…"

# Regenerate launcher icons so an edit to make_icons.py can't drift from the APK.
python3 "$REPO_ROOT/scripts/make_icons.py" >/dev/null

cd "$ANDROID_DIR"

if [ "$1" = "--clean" ]; then
    ./gradlew clean
fi

./gradlew assembleDebug

SRC="app/build/outputs/apk/debug/app-debug.apk"
DEST="$DATA_DIR/funforge.apk"

if [ ! -f "$SRC" ]; then
    echo "❌ Build succeeded but APK not found at $SRC"
    exit 1
fi

mkdir -p "$DATA_DIR"
cp "$SRC" "$DEST"

# Written last so a phone can never see a new version.json before the APK.
printf '{"versionCode": %s, "versionName": "%s", "url": "/download/funforge.apk"}\n' \
    "$VERSION_CODE" "$VERSION_NAME" > "$DATA_DIR/version.json"

SIZE=$(stat -c%s "$DEST" 2>/dev/null || stat -f%z "$DEST")
STAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo
echo "✅ APK ready: $DEST"
echo "   version:     $VERSION_NAME (code $VERSION_CODE)"
echo "   size:        $SIZE bytes"
echo "   built:       $STAMP"
echo
echo "📲 First install (manual, one time):"
echo "   1. Open http://100.69.184.113:$PORT/download/funforge.apk on the phone"
echo "   2. Tap the downloaded file → Install"
echo
echo "🔄 After that: bump version.txt, run ./build-apk.sh, and the app offers"
echo "   to update itself on next launch or resume."
echo
echo "(No uninstall required — same debug signing key as any previous build.)"
