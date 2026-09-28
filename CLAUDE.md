# Claude AI Assistant Guidelines for FunForge

## ⚠️ CRITICAL SAFETY RULES ⚠️

### NEVER DO THESE OPERATIONS

**System Operations:**
- ❌ `sudo reboot` or any system restart command
- ❌ `shutdown`, `poweroff`, `systemctl reboot`
- ❌ Any operation that restarts the entire server

**Database Operations:**
- ❌ `pg_resetwal` without verified backups
- ❌ `DROP DATABASE` without explicit permission
- ❌ Direct deletion of database files
- ❌ `TRUNCATE` on production tables

**Container Operations:**
- ❌ `docker-compose down` on production
- ❌ Stopping all containers at once
- ❌ `docker system prune -a` without permission
- ❌ Deleting volumes without backups

### ALWAYS DO THIS INSTEAD

**For Service Issues:**
```bash
# 1. Diagnose first
docker logs funforge
df -h
free -h

# 2. Restart individual service
cd /home/brandon/projects/FunForge
docker compose up -d
```

**For Database Issues:**
```bash
# 1. Check logs
docker logs funforge

# 2. Backup database first
cp data/fun_forge.db data/fun_forge.db.backup

# 3. Then proceed with fixes
```

## FunForge-Specific Guidelines

### Kid-Friendly Design Principles
- All touch targets must be at least 80px
- Use bright, contrasting colors
- Large, clear fonts (minimum 24px)
- Simple, one-tap interactions
- No complex navigation

### Music Library
- Music source: `/mnt/boston/media/kid-media/music/` (read-only mount)
- Supported formats: MP3, FLAC, M4A, AAC, OGG, WAV, WMA
- Automatic scanning on startup
- Manual scan via web interface or API

### Android app (`android/`, `build-apk.sh`)

Hand-written Java WebView wrapper around `http://100.69.184.113:8006` — the same
pattern as LifeForge and GreatReads. No Capacitor, no Play Store.

- `./build-apk.sh` builds the debug APK and stages it + `version.json` in `data/`,
  which the container serves at `/download/`. The installed app self-updates from
  there on launch and on resume.
- `versionCode` comes from `version.txt` and is forced monotonic — **never set it
  by hand**. A versionCode that goes backwards permanently disables the updater.
- `PlaybackService` is a foreground service holding a `MediaSession` and a
  `PARTIAL_WAKE_LOCK`. It does **not** request audio focus: the WebView's `<audio>`
  already owns it, and two claimants make playback stop the instant it starts.
- `MainActivity.onPause()` deliberately skips `webView.onPause()` while the service
  is running — `onPause()` suspends WebView media, which is the exact thing
  background playback must survive.
- Web changes need no rebuild (it's a shell around the live server). Only Java or
  manifest changes do.

### Parent lock

One global lock, in `static/js/funforge-lock.js`, keyed on `localStorage`
`funforge_unlocked`. Unlocking once unlocks every page until it's locked again.
Pages must not keep their own parent-mode flag — subscribe with `KidLock.onChange`
and gate actions with `KidLock.require(fn)`. PIN comes from `PARENT_PIN` (default
`1234`), injected by `base.html`; it is a toddler lock, not a security boundary.

### Screen names

The kid-facing names are **Songs** (`/music/`), **Stories** (`/podcasts/` —
podcasts and audiobooks) and **Videos** (`/videos/`). URLs and model names still
say "podcast"; only the copy says Stories. The home page is those three tiles and
nothing else — no stats, no counts.

### Keeping playback alive across the UI

Browsing must not navigate away from a page that is playing — a page load destroys
the `<audio>` element and the music stops. Albums, artists and podcast shows open
in an in-page sheet instead. Keep it that way for any new browsing surface.

### Testing

`python3 scripts/smoke_test.py` (needs playwright + chromium) drives a real browser
at phone size over the lock, the sheets and the Android media bridge. Run it after
touching the player, the lock, or the templates.

### Future Considerations
- Keep architecture extensible for movies/shows
- Plan for parental controls
- Consider time limits for video content
- Think about calendar integration

## General Guidelines

- Always ask before destructive operations
- Diagnose root cause before acting
- Restart individual services, not systems
- Verify backups before database operations
- When in doubt, ask the user

