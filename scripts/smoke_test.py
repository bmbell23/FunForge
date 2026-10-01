#!/usr/bin/env python3
"""End-to-end smoke test for FunForge's player, parent lock and Android bridge.

Drives a real browser against the running container, so it covers the things
that only break in a browser: the global parent lock, the in-page album/show
sheets, and the JS bridge the Android shell uses to keep audio alive when the
screen locks.

    pip install playwright && playwright install chromium
    python3 scripts/smoke_test.py [http://localhost:8006]

Exits non-zero on the first failure.
"""

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8006"

# Stands in for the Android shell's window.FunForge, recording every call so we
# can assert the page actually drives the foreground media service.
FAKE_BRIDGE = """
window.__bridgeCalls = [];
window.FunForge = {
  isNativeApp: () => true,
  mediaStart: (t,a,c) => window.__bridgeCalls.push(['start',t,a,c]),
  mediaMeta:  (t,a,c) => window.__bridgeCalls.push(['meta',t,a,c]),
  mediaState: (p,pos,dur,r) => window.__bridgeCalls.push(['state',p,Math.round(pos)]),
  mediaStop:  () => window.__bridgeCalls.push(['stop']),
  keepScreenOn: (on) => window.__bridgeCalls.push(['screen', on]),
};
"""

passed = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  ✓ {msg}")


def section(name):
    print(f"\n{name}")


def run(page, errors):
    def go(path, reload_after_storage=False):
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(700)

    # ---- global parent lock -------------------------------------------------
    section("Parent lock (one unlock, everywhere, until you lock again)")
    go("/")
    page.evaluate("localStorage.clear()")
    go("/")
    assert page.locator("#kidLockBtn").count() == 1, "no lock button on the home page"
    assert page.locator("#kidLockBtn").inner_text().strip() == "🔒", "did not start locked"
    ok("home page shows the lock button, starts locked")

    page.click("#kidLockBtn")
    page.wait_for_selector("#pinOverlay")
    for digit in "9999":
        page.click(f'.pin-key[data-digit="{digit}"]')
    page.wait_for_timeout(300)
    assert page.locator("#pinOverlay").count() == 1, "keypad closed on a wrong PIN"
    assert not page.evaluate("KidLock.isUnlocked()"), "a wrong PIN unlocked the app"
    ok("wrong PIN is rejected and does not unlock")

    for digit in "1234":
        page.click(f'.pin-key[data-digit="{digit}"]')
    page.wait_for_timeout(400)
    assert page.locator("#pinOverlay").count() == 0, "keypad stayed open after the right PIN"
    assert page.evaluate("KidLock.isUnlocked()"), "correct PIN did not unlock"
    ok("correct PIN unlocks")

    for path, flag in [("/music/", "parentModeActive"), ("/podcasts/", "parentModeActive")]:
        go(path)
        assert page.evaluate("KidLock.isUnlocked()"), f"lock lost navigating to {path}"
        assert page.evaluate(flag), f"{path} did not pick up parent mode"
        assert page.locator("#kidLockBtn").count() == 1, f"no lock button on {path}"
    ok("unlock survives navigation to Sounds and Podcasts")

    go("/music/")
    ran = page.evaluate("() => { let r = false; pinProtectedAction(() => { r = true; }); return r; }")
    assert ran and page.locator("#pinOverlay").count() == 0, "re-prompted while already unlocked"
    ok("protected controls run with no further prompts while unlocked")

    page.click("#kidLockBtn")
    page.wait_for_timeout(300)
    go("/music/")
    assert not page.evaluate("parentModeActive"), "still in parent mode after locking"
    ok("locking on one page locks the whole app again")

    # ---- native media bridge ------------------------------------------------
    section("Android media bridge (background playback + lock-screen controls)")
    page.evaluate("localStorage.setItem('funforge_unlocked','true')")
    go("/music/")
    assert page.evaluate("FunForgeNative.isNative"), "native bridge not detected"
    assert page.evaluate("typeof window.__mediaControl === 'function'"), "__mediaControl missing"
    ok("bridge detected and window.__mediaControl installed")

    page.locator(".song-tile").first.click()
    page.wait_for_timeout(2500)
    calls = page.evaluate("window.__bridgeCalls")
    starts = [c for c in calls if c[0] == "start"]
    assert not page.evaluate("audioPlayer.paused"), "playback never started"
    assert starts, f"mediaStart never called; calls={calls[:6]}"
    assert starts[0][3].startswith("http"), "cover URL was not made absolute for the native side"
    assert any(c[0] == "state" for c in calls), "mediaState never called"
    assert ["screen", True] in calls, "display was not held awake while playing"
    ok("playing a song starts the media session with absolute cover art")

    # Tapping a tile in parent mode *plays* it, so line up the next song with the
    # tile's ➕ instead — otherwise there's nothing for "next" to advance to.
    page.locator(".song-tile").nth(1).locator(".song-add-queue").click()
    page.wait_for_timeout(600)
    before = page.evaluate("currentQueueIndex")
    page.evaluate("window.__bridgeCalls = []")
    page.evaluate("window.__mediaControl('next')")
    page.wait_for_timeout(1500)
    assert page.evaluate("currentQueueIndex") == before + 1, "lock-screen next did not advance"
    assert any(c[0] in ("meta", "start") for c in page.evaluate("window.__bridgeCalls")), \
        "no metadata pushed after skipping"
    page.evaluate("window.__mediaControl('prev')")
    page.wait_for_timeout(1200)
    assert page.evaluate("currentQueueIndex") == before, "lock-screen prev did not go back"
    ok("lock-screen next/prev move through the queue and refresh the notification")

    page.evaluate("window.__bridgeCalls = []")
    page.evaluate("window.__mediaControl('pause')")
    page.wait_for_timeout(600)
    assert page.evaluate("audioPlayer.paused"), "lock-screen pause did nothing"
    assert ["screen", False] in page.evaluate("window.__bridgeCalls"), \
        "display stayed pinned awake after pausing"
    page.evaluate("window.__mediaControl('play')")
    page.wait_for_timeout(1200)
    assert not page.evaluate("audioPlayer.paused"), "lock-screen play did nothing"
    ok("lock-screen play/pause works")

    page.evaluate("window.__bridgeCalls = []")
    page.evaluate("clearQueue()")
    page.wait_for_timeout(600)
    assert any(c[0] == "stop" for c in page.evaluate("window.__bridgeCalls")), \
        "clearing the queue left the media notification up"
    ok("clearing the queue tears the media session down")

    # ---- in-page browsing ---------------------------------------------------
    section("In-page album / show sheets (browsing never stops the music)")
    go("/music/")
    page.click("#viewToggleBtn")            # songs -> albums
    page.wait_for_timeout(300)
    url_before = page.url
    page.locator(".album-card").first.click()
    page.wait_for_timeout(500)
    assert page.url == url_before, f"opening an album navigated away to {page.url}"
    assert page.locator("#mediaSheet").is_visible(), "album sheet did not open"
    assert page.locator(".sheet-btn-play").count() == 1, "parent has no Play All"
    ok("album opens in a sheet instead of navigating")

    page.click(".sheet-btn-play")
    page.wait_for_timeout(2500)
    assert not page.evaluate("audioPlayer.paused"), "Play All did not start playback"
    page.click("#viewToggleBtn")            # albums -> artists
    page.wait_for_timeout(300)
    page.locator(".artist-card").first.click()
    page.wait_for_timeout(1000)
    assert page.locator("#mediaSheet").is_visible(), "artist sheet did not open"
    assert not page.evaluate("audioPlayer.paused"), "music stopped while browsing"
    ok("music keeps playing while browsing albums and artists")

    page.evaluate("closeMediaSheet(); clearQueue(); localStorage.removeItem('funforge_unlocked')")
    go("/podcasts/")
    page.wait_for_timeout(800)
    assert page.evaluate("allEpisodes.length") > 0, "episode list never loaded"
    url_before = page.url
    page.locator(".album-card").first.click()
    page.wait_for_timeout(500)
    assert page.url == url_before, "opening a show navigated away"
    assert page.locator(".sheet-actions").inner_text().strip() == "", "a kid was shown bulk actions"
    page.locator(".sheet-track").first.click()
    page.wait_for_timeout(2500)
    state = page.evaluate("({q: playQueue.length, paused: audioPlayer.paused, locked: isTurnLocked()})")
    assert state["q"] == 1 and not state["paused"], f"picking an episode did not play it: {state}"
    assert state["locked"], "turn was not locked after a pick"
    ok("kid picks a podcast episode from the sheet and it plays immediately")

    # ---- parent mode vs edit mode -------------------------------------------
    # Unlocking makes you a grown-up with a player, not a librarian: play and
    # queue on every card, and nothing that can delete anything until you ask.
    section("Parent mode is not edit mode")
    page.evaluate("localStorage.setItem('funforge_unlocked','true')")
    go("/music/")
    page.wait_for_timeout(400)
    assert page.evaluate("parentModeActive && !editModeActive"), "unlocking turned edit mode on"
    tile = page.locator(".song-tile").first
    assert tile.locator(".song-add-queue").count() == 1, "no ➕ on a parent-mode song tile"
    assert tile.locator(".song-checkbox").count() == 0, "checkboxes on a plain parent-mode tile"
    assert tile.locator(".song-edit-btn").count() == 0, "gear button on a plain parent-mode tile"
    assert page.locator("#songBulkActionBar").count() == 0, "bulk delete bar without asking for it"
    ok("parent mode gives each song tile play + ➕ and nothing else")

    page.click("#viewToggleBtn")            # songs -> albums
    page.wait_for_timeout(300)
    assert page.locator(".album-queue-corner").first.is_visible(), "album cards have no ➕"
    assert page.locator(".album-dp-play-btn").first.is_visible(), "album cards have no play button"
    assert not page.locator(".parent-controls").first.is_visible(), \
        "album checkbox/gear showing without edit mode"
    ok("album cards get play + ➕, not checkboxes")

    page.click("#editModeToggleBtn")
    page.wait_for_timeout(300)
    assert page.locator(".parent-controls").first.is_visible(), "edit mode did not reveal album controls"
    assert not page.locator(".album-queue-corner").first.is_visible(), "play/queue clutter left in edit mode"
    assert page.locator("#bulkActionBar").is_visible(), "edit mode did not show the album bulk bar"
    page.click("#viewToggleBtn"); page.click("#viewToggleBtn")   # back to songs
    page.wait_for_timeout(300)
    tile = page.locator(".song-tile").first
    assert tile.locator(".song-checkbox").count() == 1, "edit mode has no song checkbox"
    tile.click()
    page.wait_for_timeout(200)
    assert page.evaluate("document.querySelectorAll('.song-checkbox:checked').length") == 1, \
        "tapping a tile in edit mode did not select it"
    assert page.evaluate("audioPlayer.paused"), "tapping a tile in edit mode started playback"
    ok("edit mode is a second, deliberate tap — then checkboxes, gears and bulk actions appear")

    page.click("#editModeToggleBtn")
    page.wait_for_timeout(300)
    assert page.locator(".song-tile").first.locator(".song-checkbox").count() == 0, \
        "leaving edit mode left the checkboxes behind"
    assert page.evaluate("parentModeActive"), "leaving edit mode also dropped parent mode"
    ok("turning edit mode off keeps you in parent mode")

    go("/podcasts/")
    page.wait_for_timeout(800)
    assert page.evaluate("parentModeActive && !editModeActive"), "Stories opened straight into edit mode"
    assert page.locator(".album-queue-corner").first.is_visible(), "show cards have no ➕"
    page.locator(".album-card").first.click()
    page.wait_for_timeout(500)
    actions = page.locator(".sheet-actions").inner_text()
    assert "⚙️" not in actions, "show sheet offered the edit gear outside edit mode"
    assert "Play All" in actions, "parent lost Play All"
    page.evaluate("closeMediaSheet()")
    page.click("#editModeToggleBtn")
    page.wait_for_timeout(300)
    page.locator(".album-card").first.click()
    page.wait_for_timeout(500)
    assert "⚙️" in page.locator(".sheet-actions").inner_text(), "edit mode did not reveal the gear"
    page.evaluate("closeMediaSheet(); clearQueue()")
    ok("Stories follows the same rule: play + ➕ by default, gear only in edit mode")

    # ---- home page ----------------------------------------------------------
    section("Home page")
    go("/")
    tiles = [el.strip() for el in page.locator(".home-tile-text").all_inner_texts()]
    assert tiles == ["Songs", "Stories", "Videos", "Games"], f"home tiles are {tiles}"
    assert page.locator(".stats-grid").count() == 0, "the library-stats grid is still there"
    assert page.evaluate("document.body.scrollHeight - innerHeight") <= 0, \
        "home page scrolls — the four doors should fit on one screen"
    # The 2x2 grid must sit centred in what the header leaves. A desktop rule for
    # the queue sidebar used to push .main-content 320px right on every page,
    # including this one, which has no sidebar at all.
    off = page.evaluate("""() => {
        const g = document.querySelector('.home').getBoundingClientRect();
        return Math.abs((g.left + g.right) / 2 - innerWidth / 2);
    }""")
    assert off <= 2, f"home grid is off-centre by {off}px"
    ok("home is four tiles (Songs / Stories / Videos / Games), centred, no scrolling")

    go("/games/")
    assert page.locator(".game-card").count() >= 1, "the Games door opens onto nothing"
    ok("the Games tile opens a picker with at least one game")

    # ---- games ---------------------------------------------------------------
    section("Games (a game you start is a game you finish)")

    def pairs_on_board():
        ids = page.eval_on_selector_all(".cm-card", "els => els.map(e => e.dataset.id)")
        groups = {}
        for i, cid in enumerate(ids):
            groups.setdefault(cid, []).append(i)
        return groups

    page.evaluate("localStorage.removeItem('funforge_unlocked')")
    go("/games/")
    page.click(".game-card")
    page.wait_for_selector(".cm-card")
    page.wait_for_timeout(500)

    assert page.locator(".cm-card").count() == 4, "first board should be the easiest (2 pairs)"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header (and its ⬅️ Home button) is visible inside a game"
    ok("a game opens full-bleed with no one-tap way out")

    # Cells must stay square — album art is square, and 1fr tracks on a phone
    # cropped the middle out of every cover.
    box = page.evaluate("""() => {
        const r = document.querySelector('.cm-card').getBoundingClientRect();
        return Math.abs(r.width - r.height);
    }""")
    assert box <= 2, f"cards are {box}px off square, covers will be cropped"
    assert page.evaluate("document.body.scrollHeight - innerHeight") <= 0, "the board scrolls"
    ok("cards are square and the board never scrolls")

    # The Android hardware Back key runs webView.goBack() regardless of what this
    # page wants (MainActivity.onKeyDown), so the shell traps history instead.
    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/cover-match/" in page.url, f"Back escaped the game — now at {page.url}"
    assert page.locator(".cm-card").count() == 4, "Back tore the board down"
    ok("Back can't leave a game, however many times it's pressed")

    groups = pairs_on_board()
    for idxs in groups.values():
        for i in idxs:
            page.locator(".cm-card").nth(i).click()
            page.wait_for_timeout(120)
        page.wait_for_timeout(320)
    assert page.locator(".cm-card-done").count() == 4, "matched pairs did not stay face up"
    ok("matching a pair locks it face up")

    page.wait_for_timeout(2400)
    assert page.locator(".cm-card").count() == 6, "clearing the board did not deal a bigger one"
    ok("clearing deals the next board up — endless, with no way to lose")

    page.click("#gameExitBtn")
    page.wait_for_timeout(300)
    assert page.locator("#pinOverlay").count() == 1, "the exit let go without a PIN"
    for digit in "9999":
        page.click(f'.pin-key[data-digit="{digit}"]')
    page.wait_for_timeout(500)
    assert "/games/cover-match/" in page.url, "a wrong PIN got out of the game"
    ok("leaving costs a PIN, and a wrong one doesn't")

    for digit in "1234":
        page.click(f'.pin-key[data-digit="{digit}"]')
    page.wait_for_timeout(900)
    assert page.url.rstrip("/").endswith("/games"), f"right PIN did not leave, at {page.url}"
    # KidLock unlocks globally on success; a game that started locked puts it back,
    # or one PIN entry would leave every later game freely exitable.
    assert not page.evaluate("KidLock.isUnlocked()"), \
        "exiting a game left the whole app unlocked"
    ok("the right PIN leaves, and re-locks the app behind it")

    # The family game only exists once photos have been synced from Immich and
    # approved, so this is conditional rather than a hard requirement.
    if page.locator(".game-card-family").count():
        page.click(".game-card-family")
        page.wait_for_selector(".cm-card")
        page.wait_for_timeout(500)
        srcs = page.eval_on_selector_all(".cm-front img", "els => els.map(e => e.getAttribute('src'))")
        assert srcs and all("/static/faces/" in s for s in srcs), \
            f"family board is not using the cached face photos: {srcs[:2]}"
        page.go_back()
        page.wait_for_timeout(300)
        assert "/games/family-match/" in page.url, "Back escaped the family game"
        ok("Match the Family deals approved photos and locks the same way")

        # Only approved photos may ever reach a board.
        import json as _json
        allowed = page.evaluate("""async () => {
            const r = await fetch('/games/api/pictures?source=family&count=24');
            return (await r.json()).pictures.map(p => p.cover);
        }""")
        page.goto(BASE + "/games/photos/", wait_until="networkidle")
        on = page.eval_on_selector_all(".fp-photo-on", "els => els.map(e => e.dataset.id)")
        assert all(any(i in c for i in on) for c in allowed), \
            "an unapproved photo was served to a game board"
        ok("unapproved photos never reach a board")
    else:
        ok("family game correctly hidden — no approved photos synced")

    # ---- tic-tac-toe ---------------------------------------------------------
    section("Tic-Tac-Toe (a kid alone can win or tie, never lose)")
    page.evaluate("localStorage.removeItem('funforge_unlocked')")
    go("/games/")
    assert page.locator(".game-card-ttt").count() == 1, "the picker has no Tic-Tac-Toe tile"
    assert "Tic-Tac-Toe" in page.locator(".game-card-ttt").inner_text(), "tile is mislabelled"
    ok("the picker shows the Tic-Tac-Toe tile")

    page.click(".game-card-ttt")
    page.wait_for_selector(".ttt-cell")
    page.wait_for_timeout(500)
    assert "/games/tic-tac-toe/" in page.url, f"tile went to {page.url}"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header is visible inside Tic-Tac-Toe"
    assert page.locator("#tttChoice").is_visible(), "no mode choice on load"
    for btn in ("#tttFriends", "#tttRobo"):
        r = page.locator(btn).bounding_box()
        assert r["width"] >= 80 and r["height"] >= 80, f"{btn} is too small to hit: {r}"
    ok("the page loads on a big two-button choice: Two friends / Robo")

    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/tic-tac-toe/" in page.url, f"Back escaped Tic-Tac-Toe — now at {page.url}"
    ok("Back can't leave Tic-Tac-Toe either")

    # Two friends: pieces alternate dog, cat, and the banner follows.
    page.click("#tttFriends")
    page.wait_for_timeout(200)
    assert not page.locator("#tttChoice").is_visible(), "the choice stayed up after picking"
    assert "🐶" in page.locator("#tttBanner").inner_text(), "dog should start"
    page.locator(".ttt-cell").nth(0).click()
    assert "🐱" in page.locator("#tttBanner").inner_text(), "banner did not pass the turn to the cat"
    page.locator(".ttt-cell").nth(0).click()       # filled: ignored
    page.locator(".ttt-cell").nth(1).click()
    pieces = page.eval_on_selector_all(".ttt-cell", "els => els.map(e => e.dataset.piece || '')")
    assert pieces[:3] == ["dog", "cat", ""], f"two-friends pieces are {pieces}"
    ok("two friends: turns alternate and a filled square ignores taps")

    # Robo, at real speed: board is square, big, and doesn't scroll.
    go("/games/tic-tac-toe/")
    page.click("#tttRobo")
    page.wait_for_timeout(200)
    dims = page.evaluate("""() => {
        const rs = [...document.querySelectorAll('.ttt-cell')].map(c => c.getBoundingClientRect());
        return {
            skew: Math.max(...rs.map(r => Math.abs(r.width - r.height))),
            small: Math.min(...rs.map(r => Math.min(r.width, r.height))),
            over: document.body.scrollHeight - innerHeight,
            font: parseFloat(getComputedStyle(document.querySelector('#tttBanner')).fontSize),
        };
    }""")
    assert dims["skew"] <= 2, f"squares are {dims['skew']}px off square"
    assert dims["small"] >= 80, f"squares are only {dims['small']}px — too small for small hands"
    assert dims["over"] <= 0, "the board scrolls"
    assert dims["font"] >= 24, f"banner font is {dims['font']}px"
    ok("the board is square, squares are 80px+, and nothing scrolls")

    page.locator(".ttt-cell").nth(4).click()
    page.locator(".ttt-cell").nth(0).click()       # Robo's turn: ignored
    pieces = page.eval_on_selector_all(".ttt-cell", "els => els.map(e => e.dataset.piece || '')")
    assert pieces.count("dog") == 1 and pieces.count("cat") == 0, f"tap during Robo's turn landed: {pieces}"
    page.wait_for_function("document.querySelectorAll('.ttt-cell[data-piece=cat]').length === 1",
                           timeout=3000)
    ok("Robo answers after a beat as the cat, and taps meanwhile are ignored")

    # 30 whole rounds, fast-forwarded, against a kid who taps at random. Robo must
    # never end one with three cats in a row, and every round must end in a
    # celebration and a fresh board.
    result = page.evaluate("""async () => {
        window.FF_TTT_FAST = true;
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const board = document.getElementById('tttBoard');
        const L = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
        const tally = { dog: 0, cat: 0, tie: 0 };
        let catLines = 0;
        for (let n = 0; n < 30; n++) {
            const round = board.dataset.round;
            for (let guard = 0; board.dataset.state !== 'cheer'; guard++) {
                if (guard > 5000) throw new Error('round never ended');
                if (board.dataset.state === 'play') {
                    const free = [...board.children].filter(c => !c.dataset.piece);
                    free[Math.floor(Math.random() * free.length)].click();
                }
                await sleep(3);
            }
            const p = [...board.children].map(c => c.dataset.piece || '');
            if (L.some(l => l.every(i => p[i] === 'cat'))) catLines++;
            tally[board.dataset.winner]++;
            for (let guard = 0; board.dataset.round === round; guard++) {
                if (guard > 2000) throw new Error('no fresh board after the celebration');
                await sleep(3);
            }
            if ([...board.children].some(c => c.dataset.piece)) throw new Error('new board not empty');
        }
        return { tally, catLines };
    }""")
    assert result["catLines"] == 0 and result["tally"]["cat"] == 0, \
        f"Robo won a round: {result}"
    assert sum(result["tally"].values()) == 30, f"rounds went missing: {result}"
    ok(f"30 rounds vs Robo, never a Robo win ({result['tally']['dog']} dog, "
       f"{result['tally']['tie']} tie), each followed by a fresh board")

    assert not errors, "JS errors in Tic-Tac-Toe: " + "; ".join(errors)
    ok("no JS errors while playing")

    # ---- player card --------------------------------------------------------
    section("Player card")
    page.evaluate("localStorage.setItem('funforge_unlocked','true')")
    go("/music/")
    card = page.locator("#nowPlaying")
    assert "player-card-idle" in (card.get_attribute("class") or ""), \
        "player card does not start idle"
    assert page.locator(".sidebar-controls").count() == 0, "the old black control strip is back"
    ok("idle player card, no separate control strip")

    # The track list arrives asynchronously. Slicing it before it lands builds an
    # empty queue, nothing plays, and every assertion below fails for a reason
    # that has nothing to do with the player — the actual cause of this test
    # failing roughly one run in three.
    page.wait_for_function("() => Array.isArray(allTracks) && allTracks.length >= 10",
                           timeout=15000)

    # Fill both kids' picks so the interleaved queue builds and starts.
    page.evaluate("""() => {
        picks[0] = allTracks.slice(0, 5);
        picks[1] = allTracks.slice(5, 10);
        buildAndStartPlayQueue();
        updateTurnIndicator(); renderQueue(); renderSongGrid();
    }""")
    # Wait on the condition, not the clock. A flat sleep here raced audio
    # start-up in headless Chromium and failed about one run in three.
    page.wait_for_function(
        """() => !audioPlayer.paused
                 && !document.getElementById('nowPlaying').classList.contains('player-card-idle')
                 && parseFloat(document.getElementById('pcBarFill').style.width || '0') > 0""",
        timeout=15000)
    assert not page.evaluate("audioPlayer.paused"), "queue did not start playing"
    assert "player-card-idle" not in (card.get_attribute("class") or ""), "card stayed idle"
    assert page.evaluate("document.getElementById('nowPlaying').style.getPropertyValue('--pc-accent')"), \
        "player card was not tinted with the current picker's colour"
    assert page.evaluate("parseFloat(document.getElementById('pcBarFill').style.width) > 0"), \
        "progress bar never moved"
    art_w = page.evaluate(
        "Math.round(document.querySelector('.pc-art').getBoundingClientRect().width)")
    assert art_w <= 100, f"player cover is {art_w}px wide — .pc-art rules are not in effect"
    assert page.evaluate("document.getElementById('pcTotal').textContent") != "0:00", \
        "track length never showed"
    ok("playing fills the card, tints it, and runs the progress bar")

    rows = page.evaluate("document.querySelectorAll('#queueList .queue-item').length")
    total = page.evaluate("playQueue.length")
    assert rows == total - 1, f"queue lists {rows} of {total} — the current track is duplicated"
    playing_title = page.evaluate("document.getElementById('nowPlayingTitle').textContent")
    listed = page.evaluate(
        "[...document.querySelectorAll('#queueList .queue-item-title')].map(e => e.textContent)")
    assert playing_title not in listed, "the playing track also appears in the queue list"
    ok("the queue lists only what's still to come, not the current track")

    for el in ["turnDot", "turnCounter"]:
        disp = page.evaluate(f"getComputedStyle(document.getElementById('{el}')).display")
        assert disp == "none", f"#{el} is still showing ({disp}) while the playlist plays"
    ok("the picking dot and counter bubble are gone once everyone has picked")

    page.evaluate("clearQueue()")
    page.wait_for_timeout(600)
    assert "player-card-idle" in (card.get_attribute("class") or ""), \
        "player card did not return to idle after Stop"
    ok("Stop returns the card to its idle state")

    # ---- asset freshness ----------------------------------------------------
    # A stale stylesheet paired with fresh HTML drew the home tiles as bare links
    # and the album cover at its natural 800px. Nothing on /static sent
    # Cache-Control, so the WebView kept its copy indefinitely.
    section("Asset freshness")
    go("/")
    links = page.evaluate("""() => ({
        css: [...document.querySelectorAll('link[rel=stylesheet]')].map(l => l.getAttribute('href')),
        js: [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src')),
    })""")
    for href in links["css"] + links["js"]:
        assert "?v=" in href, f"{href} is not cache-busted — a stale copy can outlive a deploy"
    ok("stylesheet and scripts are versioned by file mtime")

    headers = {}
    def grab(resp):
        headers[resp.url] = resp.headers.get("cache-control", "")
    page.on("response", grab)
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.remove_listener("response", grab)
    html_cc = next((v for k, v in headers.items() if k.rstrip("/") == BASE.rstrip("/")), None)
    css_cc = next((v for k, v in headers.items() if "style.css" in k), None)
    assert css_cc and "immutable" in css_cc, f"versioned CSS cache-control is {css_cc!r}"
    assert html_cc == "no-cache", f"HTML cache-control is {html_cc!r} — cached pages mean cached ?v= stamps"
    ok("versioned assets cache forever, pages always revalidate")

    # The real end-to-end guard: are the rules the markup depends on in effect?
    tile_bg = page.evaluate(
        "getComputedStyle(document.querySelector('.home-tile')).backgroundImage")
    assert "gradient" in tile_bg, f"home tiles are unstyled (background-image: {tile_bg})"
    ok("home tiles are actually styled by the served stylesheet")

    # ---- layout -------------------------------------------------------------
    section("Layout")
    for path in ["/", "/music/", "/podcasts/", "/videos/", "/music/albums/1", "/podcasts/shows/1"]:
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(300)
        over = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 0, f"{path} overflows horizontally by {over}px at this width"
    ok("no page scrolls sideways")

    assert not errors, "JS errors: " + "; ".join(errors)
    ok("no uncaught JS errors on any page")


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        # Phone-sized, because that's what this actually runs on.
        ctx = browser.new_context(**pw.devices["Pixel 5"])
        ctx.add_init_script(FAKE_BRIDGE)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            run(page, errors)
        finally:
            browser.close()
    print(f"\n{passed} checks passed against {BASE}")


if __name__ == "__main__":
    main()
