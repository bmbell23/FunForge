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

### Parent mode is not edit mode

Unlocking makes you a grown-up with a player, not a librarian. In plain parent
mode every card offers exactly two things: tap it to play, or tap the ➕ in its
top-right corner to queue it. Checkboxes, ⚙️ buttons and the bulk edit/delete
bars live behind a second, deliberate tap — the ✏️ button (`#editModeToggleBtn`,
`editModeActive`) that appears next to the lock in parent mode. Edit mode is
never restored from storage: it starts off on every page load, and locking drops
both modes at once. Anything destructive added later belongs behind that toggle.
Play/queue buttons sit in the *corners* of album art, never centred — a button
under the middle of a card turns "show me what's in here" into "play the lot".

### Screen names

The kid-facing names are **Songs** (`/music/`), **Stories** (`/podcasts/` —
podcasts and audiobooks), **Videos** (`/videos/`) and **Games** (`/games/`). URLs
and model names still say "podcast"; only the copy says Stories. The home page is
those four tiles and nothing else — no stats, no counts.

The tiles are a 2x2 grid that does **not** reflow between phone and desktop, and
the order is fixed. A kid who can't read navigates by position, so Songs must stay
top-left on every screen it ever renders on. Adding a fifth screen means rethinking
the shape, not appending a tile.

### Games

Games and the media player are **separate worlds**. A game brings its own audio,
and arriving on a game page has already destroyed the queue's `<audio>` — so a
game owns audio outright and never ducks, mixes or shares. This is the one place
in the app where a page load stopping the music is the *point* rather than the
bug the sheets exist to avoid.

One route per game (`routes/games.py`), the same shape `videos.py` uses. There is
deliberately no scanned `games/` directory or manifest: that plugin layer gets
built when the duplication between two real games makes its shape obvious, not
before.

Every game extends `games/_shell.html`, which supplies three things so no game
has to remember them:

- **A locked exit.** Starting a game is a commitment — you cannot back out and
  pick another. Without that, a toddler spends the session flipping between games
  instead of playing one. Leaving costs the `KidLock` PIN. If the app was locked
  when the game started, the shell **re-locks it on the way out**: `KidLock`
  unlocks globally on success, so otherwise one PIN entry would leave every later
  game freely exitable.
- **A back button that stays shut.** `MainActivity.onKeyDown` runs
  `webView.goBack()` whenever history allows and cannot see anything the page
  believes about being locked, so the shell pushes a sentinel history entry and
  re-pushes it on every `popstate`. Keeping *some* history entry matters in both
  directions: with none, `canGoBack()` is false and Back closes the whole app.
  A JS-bridge flag checked in Java would be sturdier, but it needs an APK rebuild;
  the history trap ships with web changes, which are live immediately.
- **Pause on hide**, via `FunForgeGame.onPause` / `onResume`.

No scores, no timers, no fail states. A game that can be lost produces a tantrum,
not a retry. Clearing a board deals a slightly bigger one and the last size
repeats forever. There is **no app-level two-kid turn taking** the way Songs has —
if a game wants turns or co-op, it builds them itself.

Game boards size their cells in JS, not CSS. Album art is square, and `1fr` grid
tracks on a phone produced 200x450 cells whose `object-fit: cover` sliced the
middle out of every cover — the exact part a kid matches on.

### Family photos (Immich)

Match the Family is the same game as Match the Covers with a different picture
source — `/games/api/pictures?source=family`. Adding a third source should mean
another route, not another copy of `games/match.html`.

**The app never talks to Immich.** `scripts/sync_immich_faces.py` runs on the
*host* and caches thumbnails into `static/faces/` plus a manifest at
`data/family_photos.json`; FunForge only ever reads its own disk. Two reasons:

- The container is on its own bridge and genuinely cannot reach Immich — Docker's
  isolation rules drop cross-bridge traffic, and the host's published 2283 is not
  bound in a way a container can use. Verified, not assumed.
- The games keep working while Immich is down, upgrading, or has had its port
  mangled by a stale DNAT rule (which it had, on 21 Sep 2026).

The script is standard-library only so the image needs no HTTP client.

**Nothing synced is approved.** Face recognition returns *every* photo of a child,
bath time included, so `approved` defaults to false and a grown-up ticks photos
off at `/games/photos/` before they can reach a board. The picker hides the family
tile until at least two photos are approved — a tile that opens onto an empty
board is worse than no tile, because tapping it locks a kid into nothing.

**This repo is public.** `static/faces/` is gitignored and must stay that way.
Pictures of the kids do not go to GitHub.

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

