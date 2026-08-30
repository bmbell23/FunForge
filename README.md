# KidMedia 🎵

A kid-friendly, touchscreen-optimized media server designed for young children (2+ years old). Built with Python, FastAPI, and a colorful, large-button interface perfect for little hands.

## Overview

KidMedia is a custom media server similar to the other Forge projects (ArtForge, LifeForge, CodeForge, etc.) that provides a safe, simple, and fun way for young children to browse and play music on a touchscreen device.

### MVP Features (Phase 1)

- **Music Streaming**: Browse and play music from `/mnt/boston/media/kid-media/music/`
- **Kid-Friendly UI**: Large, colorful buttons and text optimized for touchscreen
- **Album Browsing**: Visual album grid with cover art
- **Music Player**: Simple playback controls (play, pause, next, previous)
- **Auto-scanning**: Automatically scans music library on startup
- **Metadata Support**: Extracts artist, album, track info from audio files

### Supported Audio Formats

- MP3
- FLAC
- M4A
- AAC
- OGG
- WAV
- WMA

## Architecture

KidMedia follows the same architecture pattern as other Forge projects:

```
KidMedia/
├── src/kid_media/          # Python package
│   ├── models/             # SQLAlchemy database models
│   │   ├── album.py
│   │   ├── artist.py
│   │   └── track.py
│   ├── routes/             # FastAPI routes
│   │   └── music.py
│   ├── services/           # Business logic
│   │   └── scanner.py      # Music library scanner
│   ├── static/             # Static assets (CSS, JS, images)
│   │   ├── css/
│   │   └── js/             # kidmedia-lock.js, kidmedia-media.js
│   ├── templates/          # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── index.html
│   │   └── music/
│   ├── config.py           # Configuration settings
│   ├── templating.py       # Shared Jinja2 environment
│   ├── database.py         # Database setup
│   └── main.py             # FastAPI application
├── android/                # Android WebView wrapper (see "Android app")
├── scripts/                # make_icons.py (launcher art), smoke_test.py
├── data/                   # SQLite DB, kidmedia.apk, version.json (gitignored)
├── logs/                   # Application logs
├── build-apk.sh            # Build + stage the Android APK
├── version.txt             # Single source of truth for the app version
├── docker-compose.yml      # Docker Compose configuration
├── Dockerfile              # Multi-stage Docker build
└── pyproject.toml          # Python package configuration
```

## Setup & Installation

### Prerequisites

- Docker and Docker Compose
- Music files in `/mnt/boston/media/kid-media/music/`

### Quick Start

1. **Build and start the container:**
   ```bash
   cd /home/brandon/projects/KidMedia
   docker-compose up -d --build
   ```

2. **Check the logs:**
   ```bash
   docker logs -f kidmedia
   ```

3. **Access the application:**
   - Open your browser to `http://localhost:8006`
   - Or access via your server's IP: `http://your-server-ip:8006`

### Initial Library Scan

The application automatically scans the music library on startup. You can also trigger a manual scan:

- Click the "Scan Library" button in the web interface
- Or use the API: `POST http://localhost:8006/api/scan`

## Usage

### For Kids

1. **Home Screen**: Shows how many songs, albums, and artists are available
2. **Browse Music**: Tap the big "Browse Music" button
3. **Select Album**: Tap on any album cover to see the songs
4. **Play Song**: Tap on any song to start playing
5. **Player Controls**: Use the big ▶️ ⏸️ ⏭️ buttons to control playback

### Design Features for Young Children

- **Large Touch Targets**: All buttons are 80px+ for easy tapping
- **Colorful Track Numbers**: Each track number has a different bright color for visual identification
- **Simple Navigation**: Minimal clicks to get to music
- **Auto-play Next**: Songs automatically play in sequence
- **No Text Input Required**: Everything is tap-based

## Android app

KidMedia ships as a native Android wrapper around the same web app — the pattern
used by LifeForge and GreatReads. There is no Play Store and no Capacitor: it is a
hand-written Java `WebView` shell that loads `http://100.69.184.113:8006` over
Tailscale.

What the wrapper adds beyond a browser tab:

- **Background playback.** A foreground `PlaybackService` holds a `MediaSession`
  and a `PARTIAL_WAKE_LOCK`, so music keeps playing when the screen locks. Without
  it Android freezes the WebView's process a few minutes after the screen goes
  off and the stream dies mid-song.
- **Lock-screen / headphone / Bluetooth controls.** The notification's
  previous / play-pause / next buttons — and hardware media keys — are routed back
  into the web player through `window.__mediaControl`.
- **Self-updating.** On launch and on resume the app fetches
  `/download/version.json` and offers to install a newer APK via `PackageInstaller`.

### Building and installing

```bash
cd /home/brandon/projects/KidMedia
./build-apk.sh              # or ./build-apk.sh --clean
```

That builds a debug APK and stages it (with its `version.json`) in `data/`, which
the container serves at `/download/`. First install, once per device:

1. Open `http://100.69.184.113:8006/download/kidmedia.apk` in the phone's browser
2. Tap the download → Install

After that, bump `version.txt`, run `./build-apk.sh`, and the installed app offers
the update itself. Every build is signed with the shared `~/.android/debug.keystore`,
so updates install in place — no uninstall, no lost data.

`versionCode` is derived from `version.txt` (`major*10000 + minor*100 + patch`) and
can never decrease: if the derived number is at or behind what's already published,
the script uses `published + 1` instead. A versionCode that went backwards would
silently disable the self-updater forever.

## Parent lock

A single lock button (🔒 / 🔓) sits in the header of every page. Enter the PIN once
on the touch keypad and the whole app is unlocked — parent mode on the Sounds and
Podcasts pages, playback controls, search, bulk edit and delete — across page
navigation and app restarts, until you tap the button again to lock. An orange strip
along the top edge shows at a glance that it's unlocked.

The PIN defaults to `1234` and is set with the `PARENT_PIN` environment variable. It
is client-side by design: it keeps a three-year-old out of the delete buttons, and it
is not an authentication boundary.

## Testing

```bash
pip install playwright && playwright install chromium
python3 scripts/smoke_test.py          # against http://localhost:8006
```

Drives a real browser at phone size and checks the parent lock, the in-page album
and show sheets, and the Android media bridge (using a stub for `window.KidMedia`).

## Configuration

Edit environment variables in `docker-compose.yml`:

- `DATABASE_URL`: SQLite database location (default: `sqlite:///./data/kid_media.db`)
- `MUSIC_DIR`: Music library path (default: `/music`)
- `PORT`: Application port (default: `8006`)
- `PARENT_PIN`: PIN for the parent lock (default: `1234`)

## Future Features (Not in MVP)

- **Movies & Shows**: Video playback with parental time limits
- **Parental Controls**: Password-protected settings
- **Calendar Integration**: Skylight-style family calendar
- **Screensaver Mode**: Display artwork when idle
- **Metadata Editing**: Edit album/track information
- **Playlists**: Create custom playlists for kids

## Technology Stack

- **Backend**: Python 3.11, FastAPI, SQLAlchemy
- **Database**: SQLite
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Audio**: HTML5 Audio API
- **Metadata**: Mutagen library for audio tag extraction
- **Container**: Docker with multi-stage builds

## Port Assignment

- **KidMedia**: Port 8006
- Other Forge projects use: 8000-8005, 8007, 8009

## Troubleshooting

### Music not showing up?

1. Check that music files exist in `/mnt/boston/media/kid-media/music/`
2. Trigger a manual scan via the web interface
3. Check logs: `docker logs kidmedia`

### Container won't start?

1. Check logs: `docker logs kidmedia`
2. Verify port 8006 is not in use: `ss -tulpn | grep 8006`
3. Check disk space: `df -h`

### Audio won't play?

1. Verify the audio file format is supported
2. Check browser console for errors
3. Try a different browser (Chrome/Firefox recommended)

## Development

To run in development mode with hot-reload:

```bash
cd /home/brandon/projects/KidMedia
docker-compose up --build
```

Changes to Python files will automatically reload the server.

## License

Private project for personal use.

## Author

Brandon Bell - brandon@forge-freedom.com

