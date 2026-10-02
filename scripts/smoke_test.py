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

    # ---- checkers ------------------------------------------------------------
    section("Checkers (tap a piece, tap a glow)")
    page.evaluate("localStorage.removeItem('funforge_unlocked')")
    go("/games/")
    assert page.locator(".game-card-chk").count() == 1, "the picker has no Checkers tile"
    assert "Checkers" in page.locator(".game-card-chk").inner_text(), "tile is mislabelled"
    ok("the picker shows the Checkers tile")

    errors.clear()
    page.click(".game-card-chk")
    page.wait_for_selector(".chk-square")
    page.wait_for_timeout(500)
    assert "/games/checkers/" in page.url, f"tile went to {page.url}"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header is visible inside Checkers"
    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/checkers/" in page.url, f"Back escaped Checkers — now at {page.url}"
    ok("Back can't leave Checkers either")

    dims = page.evaluate("""() => {
        const rs = [...document.querySelectorAll('.chk-square')].map(c => c.getBoundingClientRect());
        return {
            n: rs.length,
            skew: Math.max(...rs.map(r => Math.abs(r.width - r.height))),
            small: Math.min(...rs.map(r => Math.min(r.width, r.height))),
            over: document.body.scrollHeight - innerHeight,
            dogs: document.querySelectorAll('.chk-square[data-piece=dog]').length,
            cats: document.querySelectorAll('.chk-square[data-piece=cat]').length,
        };
    }""")
    assert dims["n"] == 64, f"board has {dims['n']} squares"
    assert dims["skew"] <= 2, f"squares are {dims['skew']}px off square"
    assert dims["small"] >= 40, f"squares are only {dims['small']}px"
    assert dims["over"] <= 0, "the board scrolls"
    assert dims["dogs"] == 12 and dims["cats"] == 12, f"opening has {dims['dogs']} dogs, {dims['cats']} cats"
    ok(f"8x8 square board, {dims['small']:.0f}px squares, 12 dogs v 12 cats, no scrolling")

    sq = page.locator(".chk-square")
    def glowing():
        return page.eval_on_selector_all(
            ".chk-square", "els => els.flatMap((e, i) => e.classList.contains('chk-target') ? [i] : [])")
    assert "🐶" in page.locator("#chkBanner").inner_text(), "dog should start"
    sq.nth(17).click()                                  # a cat, on the dog's turn: ignored
    assert glowing() == [], "picking the other side's piece lit up moves"
    sq.nth(40).click()
    assert glowing() == [33], f"dog at 40 should only glow 33, got {glowing()}"
    sq.nth(33).click()
    assert page.locator(".chk-square").nth(33).get_attribute("data-piece") == "dog", "dog did not move"
    assert "🐱" in page.locator("#chkBanner").inner_text(), "turn did not pass to the cat"
    ok("tap a piece, its moves glow, tap a glow to move, and the turn passes")

    sq.nth(19).click()
    sq.nth(26).click()
    sq.nth(33).click()
    assert sorted(glowing()) == [19, 24], f"dog at 33 should glow the jump 19 and the step 24, got {glowing()}"
    sq.nth(19).click()
    pieces = page.eval_on_selector_all(".chk-square", "els => els.map(e => e.dataset.piece || '')")
    assert pieces[26] == "" and pieces[19] == "dog" and pieces.count("cat") == 11, "the jump did not capture"
    ok("jumping a cat takes it off the board")

    # Double jump: a dog at 49 with cats at 42 and 28 lined up behind each other.
    # A spare cat at 1 keeps the round going.
    chain_board = """() => FF_CHK_DEAL({ 49: { who: 'dog', king: false }, 42: { who: 'cat', king: false },
                                      28: { who: 'cat', king: false }, 1: { who: 'cat', king: false } })"""
    def chaining():
        return page.locator("#chkBoard").get_attribute("data-chain") == "1"
    page.evaluate(chain_board)
    sq.nth(49).click()
    assert sorted(glowing()) == [35, 40], f"dog at 49 should glow the jump 35 and the step 40, got {glowing()}"
    sq.nth(35).click()
    assert chaining() and glowing() == [21], f"a second jump should be offered from 35, got {glowing()}"
    assert "🐶" in page.locator("#chkBanner").inner_text(), "the turn passed mid double-jump"
    sq.nth(40).click()                                  # not a jump: ignored
    assert chaining() and glowing() == [21], "a stray tap broke off the double jump"
    sq.nth(21).click()
    pieces = page.eval_on_selector_all(".chk-square", "els => els.map(e => e.dataset.piece || '')")
    assert pieces[21] == "dog" and pieces.count("cat") == 1, f"the double jump didn't take both cats: {pieces}"
    assert not chaining() and "🐱" in page.locator("#chkBanner").inner_text(), "turn didn't pass after the chain"
    ok("a jump that can jump again keeps going, and both cats come off")

    page.evaluate(chain_board)
    sq.nth(49).click()
    sq.nth(35).click()
    sq.nth(35).click()                                  # tap the jumper: stop here
    pieces = page.eval_on_selector_all(".chk-square", "els => els.map(e => e.dataset.piece || '')")
    assert pieces[28] == "cat" and not chaining(), "stopping a double jump didn't leave the second cat"
    assert "🐱" in page.locator("#chkBanner").inner_text(), "stopping a double jump didn't pass the turn"
    ok("tapping the jumper stops the chain, since capturing is never forced")

    # Kinging, and the end of a round: a lone dog one step from the top, and a
    # cat with nowhere to go. Crowning ends it; then a fresh board is dealt.
    page.evaluate("""() => { window.FF_CHK_FAST = true;
        FF_CHK_DEAL({ 10: { who: 'dog', king: false }, 56: { who: 'cat', king: false } }); }""")
    sq.nth(10).click()
    assert sorted(glowing()) == [1, 3], f"lone dog should glow 1 and 3, got {glowing()}"
    sq.nth(1).click()
    assert sq.nth(1).get_attribute("data-king") == "1", "reaching the top row didn't crown the dog"
    assert page.locator("#chkBoard").get_attribute("data-winner") == "dog", "a cat with no moves didn't end the round"
    page.wait_for_function("document.querySelectorAll('.chk-square[data-piece]').length === 24", timeout=3000)
    ok("the far row crowns a piece, no moves left ends the round, and a fresh board follows")

    assert not errors, "JS errors in Checkers: " + "; ".join(errors)
    ok("no JS errors while playing")

    # ---- spell it ------------------------------------------------------------
    section("Spell It (a picture, its letters, a garden that only grows)")
    page.evaluate("localStorage.removeItem('funforge_unlocked')")
    go("/games/")
    assert page.locator(".game-card-spell").count() == 1, "the picker has no Spell It tile"
    assert "Spell It" in page.locator(".game-card-spell").inner_text(), "tile is mislabelled"
    ok("the picker shows the Spell It tile")

    errors.clear()
    page.click(".game-card-spell")
    page.wait_for_selector(".spell-tile")
    page.wait_for_timeout(500)
    assert "/games/spell/" in page.url, f"tile went to {page.url}"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header is visible inside Spell It"
    board = page.locator("#spellBoard")
    word = board.get_attribute("data-word")
    assert len(word) == 3, f"the first word should be three letters, got {word!r}"
    assert page.locator(".spell-slot").count() == 3, "one slot per letter"
    ok(f"the page loads on a three-letter word ({word})")

    dims = page.evaluate("""() => {
        const t = [...document.querySelectorAll('.spell-tile')];
        const r = t.map(e => e.getBoundingClientRect());
        return {
            n: t.length,
            w: Math.min(...r.map(x => x.width)),
            h: Math.min(...r.map(x => x.height)),
            font: Math.min(...t.map(e => parseFloat(getComputedStyle(e).fontSize))),
            hear: document.getElementById('spellHear').getBoundingClientRect().width,
            over: document.documentElement.scrollWidth - innerWidth,
            tall: document.body.scrollHeight - innerHeight,
        };
    }""")
    assert 6 <= dims["n"] <= 8, f"{dims['n']} letter tiles — should be 6 to 8, not the alphabet"
    assert dims["w"] >= 80 and dims["h"] >= 80, f"tiles are only {dims['w']}x{dims['h']}"
    assert dims["font"] >= 32, f"tile font is {dims['font']}px"
    assert dims["hear"] >= 80, f"the 🔊 button is {dims['hear']}px wide"
    assert dims["over"] <= 0 and dims["tall"] <= 0, f"the page scrolls: {dims}"
    ok(f"{dims['n']} letter tiles, 80px+ with a {dims['font']:.0f}px font; nothing scrolls")

    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/spell/" in page.url, f"Back escaped Spell It — now at {page.url}"
    ok("Back can't leave Spell It either")

    def state():
        return page.evaluate("""() => { const b = document.getElementById('spellBoard');
            return { word: b.dataset.word, filled: +b.dataset.filled, next: b.dataset.next,
                     state: b.dataset.state, round: +b.dataset.round,
                     garden: +document.getElementById('spellGarden').dataset.count }; }""")

    def tap_letter(ch):
        page.locator(f'.spell-tile[data-letter="{ch}"]').click()

    # A wrong letter: nothing fills, nothing is lost, the tile just wiggles.
    before = state()
    wrong = page.evaluate("""() => { const b = document.getElementById('spellBoard');
        const t = [...document.querySelectorAll('.spell-tile')].find(e => e.dataset.letter !== b.dataset.next);
        return t.dataset.letter; }""")
    tap_letter(wrong)
    page.wait_for_timeout(100)
    after = state()
    assert after == before, f"a wrong letter changed the board: {before} -> {after}"
    assert page.locator(".spell-tile-wiggle").count() == 1, "the wrong tile did not wiggle"
    assert page.locator(".spell-slot-filled").count() == 0, "a wrong letter filled a slot"
    ok("a wrong letter wiggles and changes nothing")

    # Right letters in order: each fills the next slot and grows a flower.
    word = before["word"]
    for i, ch in enumerate(word[:-1]):
        tap_letter(ch)
        st = state()
        assert st["filled"] == i + 1 and st["garden"] == i + 1, f"after {ch}: {st}"
    slot_text = page.eval_on_selector_all(".spell-slot", "els => els.map(e => e.textContent)")
    assert slot_text == list(word[:-1]) + [""], f"slots read {slot_text}"
    ok("right letters fill the slots in order, one flower each")

    tap_letter(word[-1])
    assert state()["state"] == "cheer", "the finished word did not celebrate"
    assert page.locator(".spell-bit").count() > 0, "no confetti"
    page.locator(".spell-tile").first.click()      # during the celebration: ignored
    assert state()["filled"] == len(word), "a tap during the celebration changed the board"
    ok("a finished word celebrates with confetti, and taps meanwhile are ignored")

    page.wait_for_function("document.getElementById('spellBoard').dataset.round === '2'", timeout=8000)
    st = state()
    assert st["state"] == "play" and st["filled"] == 0, f"the next word was not dealt fresh: {st}"
    assert st["garden"] == len(word), "the garden lost flowers between words"
    ok("about three seconds later the next word is dealt; the garden keeps its flowers")

    # Ten words, fast-forwarded, tapping the right letters: no errors, and the
    # ladder widens (3 letters, then 4 joins after 5 words, then 5 after 10).
    errors.clear()
    result = page.evaluate("""async () => {
        window.FF_SPELL_FAST = true;
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const b = document.getElementById('spellBoard');
        const lens = [];
        for (let n = 0; n < 15; n++) {
            const round = b.dataset.round;
            const word = b.dataset.word;
            lens.push(word.length);
            for (const ch of word) {
                const t = [...document.querySelectorAll('.spell-tile')].find(e => e.dataset.letter === ch);
                if (!t) throw new Error('no tile for ' + ch + ' in ' + word);
                t.click();
            }
            if (b.dataset.state !== 'cheer') throw new Error(word + ' did not complete');
            for (let g = 0; b.dataset.round === round; g++) {
                if (g > 2000) throw new Error('no new word after ' + word);
                await sleep(3);
            }
        }
        return lens;
    }""")
    # The page was already one word in, so word i is dealt after i + 1 completions.
    assert all(n == 3 for n in result[:4]), f"early words should be 3 letters: {result}"
    assert max(result[4:9]) <= 4, f"five-letter words came too early: {result}"
    assert max(result) <= 5, f"words longer than five: {result}"
    ok(f"15 words completed fast; lengths {''.join(map(str, result))}")

    # Five-letter words at phone size: nothing overflows sideways.
    for _ in range(40):
        if len(page.get_attribute("#spellBoard", "data-word")) == 5:
            break
        word = page.get_attribute("#spellBoard", "data-word")
        for ch in word:
            tap_letter(ch)
        page.wait_for_function("document.getElementById('spellBoard').dataset.state === 'play'", timeout=3000)
    word = page.get_attribute("#spellBoard", "data-word")
    assert len(word) == 5, "never dealt a five-letter word after ten completed"
    fit = page.evaluate("""() => {
        const rs = [...document.querySelectorAll('.spell-slot')].map(e => e.getBoundingClientRect());
        return { over: document.documentElement.scrollWidth - innerWidth,
                 left: Math.min(...rs.map(r => r.left)), right: Math.max(...rs.map(r => r.right)),
                 w: innerWidth, slot: rs[0].width,
                 tiles: [...document.querySelectorAll('.spell-tile')].every(e => {
                     const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }) };
    }""")
    assert fit["over"] <= 0 and fit["left"] >= 0 and fit["right"] <= fit["w"] and fit["tiles"], \
        f"a five-letter word overflows the phone: {fit}"
    ok(f"a five-letter word ({word}) fits the phone: slots {fit['slot']:.0f}px wide, no sideways scroll")

    assert not errors, "JS errors in Spell It: " + "; ".join(errors)
    ok("no JS errors while playing")

    # ---- bubble pop ----------------------------------------------------------
    section("Bubble Pop (every tap pops something, nothing can go wrong)")
    go("/games/")
    assert page.locator(".game-card-bub").count() == 1, "the picker has no Bubbles tile"
    assert "Bubbles" in page.locator(".game-card-bub").inner_text(), "Bubbles tile is mislabelled"
    r = page.locator(".game-card-bub").bounding_box()
    assert r["width"] >= 80 and r["height"] >= 80, f"the Bubbles tile is too small to hit: {r}"
    ok("the picker shows the Bubbles tile")

    page.click(".game-card-bub")
    page.wait_for_selector("#bubStage")
    page.wait_for_timeout(700)
    assert "/games/bubbles/" in page.url, f"tile went to {page.url}"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header is visible inside Bubble Pop"
    n = page.locator(".bub-bubble").count()
    assert 6 <= n <= 10, f"{n} bubbles on screen at the start, want roughly 6-10"
    sizes = page.eval_on_selector_all(
        ".bub-bubble[data-kind=wander]", "els => els.map(e => e.getBoundingClientRect().width)")
    assert sizes and all(59 <= s <= 141 for s in sizes), f"bubble sizes outside 60-140px: {sizes}"
    assert page.evaluate("document.body.scrollHeight - innerHeight") <= 0, "Bubble Pop scrolls"
    ok(f"the page loads with {n} bubbles, 60-140px, and no scrolling")

    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/bubbles/" in page.url, f"Back escaped Bubble Pop — now at {page.url}"
    ok("Back can't leave Bubble Pop either")

    def stage_num(attr):
        return int(page.evaluate(f"document.getElementById('bubStage').dataset.{attr}"))

    # Pick a bubble whose centre is on screen and outside every other bubble's
    # (fattened) hit area, so the tap can only mean that bubble.
    target = page.evaluate("""() => {
        const st = document.getElementById('bubStage').getBoundingClientRect();
        const all = [...document.querySelectorAll('.bub-bubble')].map(e => {
            const r = e.getBoundingClientRect();
            return { id: e.dataset.id, x: r.left + r.width / 2, y: r.top + r.height / 2, r: r.width / 2 };
        });
        return all.find(b =>
            b.x > st.left + 10 && b.x < st.right - 10 && b.y > st.top + 10 && b.y < st.bottom - 10 &&
            all.every(o => o.id === b.id || Math.hypot(o.x - b.x, o.y - b.y) > o.r + 14)) || null;
    }""")
    assert target, "no clear bubble to tap"
    pops_before, spawned_before = stage_num("pops"), stage_num("spawned")
    page.mouse.click(target["x"], target["y"])
    page.wait_for_timeout(100)
    assert page.locator(f".bub-bubble[data-id='{target['id']}']").count() == 0, "the tapped bubble is still there"
    assert stage_num("pops") == pops_before + 1, "the pop was not counted"
    assert page.locator(".bub-bit").count() > 0, "no burst of particles when it popped"
    ok("tapping a bubble pops it, with a burst")

    page.wait_for_function(
        f"+document.getElementById('bubStage').dataset.spawned > {spawned_before}", timeout=6000)
    page.wait_for_function("document.querySelectorAll('.bub-bubble').length >= 6", timeout=6000)
    ok("new bubbles keep rising after one pops")

    # Empty space grows a small bubble under the finger.
    spot = page.evaluate("""() => {
        const st = document.getElementById('bubStage').getBoundingClientRect();
        const bs = [...document.querySelectorAll('.bub-bubble')].map(e => e.getBoundingClientRect());
        for (let i = 0; i < 400; i++) {
            const x = st.left + 40 + Math.random() * (st.width - 80);
            const y = st.top + 40 + Math.random() * (st.height - 80);
            if (bs.every(r => Math.hypot(x - (r.left + r.width / 2), y - (r.top + r.height / 2)) > r.width / 2 + 40))
                return { x, y };
        }
        return null;
    }""")
    assert spot, "no empty space to tap"
    before = page.locator(".bub-bubble[data-kind=finger]").count()
    page.mouse.click(spot["x"], spot["y"])
    page.wait_for_timeout(100)
    assert page.locator(".bub-bubble[data-kind=finger]").count() == before + 1, \
        "tapping empty space did not make a bubble"
    ok("tapping empty space makes a small bubble under the finger")

    # Several fingers at once: three simultaneous pointers, each with its own id.
    pops_before = stage_num("pops")
    page.evaluate("""() => {
        const st = document.getElementById('bubStage');
        const bs = [...document.querySelectorAll('.bub-bubble')].slice(0, 3).map(e => e.getBoundingClientRect());
        bs.forEach((r, i) => st.dispatchEvent(new PointerEvent('pointerdown', {
            pointerId: 20 + i, pointerType: 'touch', isPrimary: i === 0, bubbles: true, cancelable: true,
            clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 })));
    }""")
    assert stage_num("pops") - pops_before >= 2, "simultaneous touches were not all handled"
    ok("several fingers at once each pop their own bubble")

    st_box = page.locator("#bubStage").bounding_box()
    for _ in range(50):
        import random as _random
        page.mouse.click(st_box["x"] + _random.uniform(2, st_box["width"] - 2),
                         st_box["y"] + _random.uniform(2, st_box["height"] - 2))
    page.wait_for_timeout(500)
    n = page.locator(".bub-bubble").count()
    assert 1 <= n <= 14, f"{n} bubbles after the mashing — should stay tidy"
    assert "/games/bubbles/" in page.url, "mashing left the game"
    assert not errors, "JS errors in Bubble Pop: " + "; ".join(errors)
    ok("50 rapid taps at random spots: no JS errors, still a tidy screenful")

    # ---- animal sounds -------------------------------------------------------
    section("Animal Sounds (six huge buttons, each one answers)")
    original_size = page.viewport_size
    page.set_viewport_size({"width": 412, "height": 860})
    go("/games/")
    assert page.locator(".game-card-ani").count() == 1, "the picker has no Animals tile"
    assert "Animals" in page.locator(".game-card-ani").inner_text(), "Animals tile is mislabelled"
    # With six or so tiles the picker may scroll on a phone; that is fine, but
    # the tiles and their text must not shrink to fit.
    for sel in (".game-card-bub", ".game-card-ani"):
        r = page.locator(sel).bounding_box()
        assert r["width"] >= 80 and r["height"] >= 80, f"{sel} is too small to hit: {r}"
    fs = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.game-card-ani .game-card-text')).fontSize)")
    assert fs >= 24, f"picker text is {fs}px"
    ok("the picker shows the Animals tile")

    page.click(".game-card-ani")
    page.wait_for_selector(".ani-tile")
    page.wait_for_timeout(500)
    assert "/games/animals/" in page.url, f"tile went to {page.url}"
    assert page.evaluate("getComputedStyle(document.querySelector('.header')).display") == "none", \
        "the shared header is visible inside Animal Sounds"
    assert page.locator(".ani-tile").count() == 6, "Animal Sounds should have six animals"
    ok("the page loads on six animals")

    for _ in range(4):
        page.go_back()
        page.wait_for_timeout(250)
    assert "/games/animals/" in page.url, f"Back escaped Animal Sounds — now at {page.url}"
    ok("Back can't leave Animal Sounds either")

    def check_fit(label):
        dims = page.evaluate("""() => {
            const rs = [...document.querySelectorAll('.ani-tile')].map(t => t.getBoundingClientRect());
            return {
                small: Math.min(...rs.map(r => Math.min(r.width, r.height))),
                skew: Math.max(...rs.map(r => Math.abs(r.width - r.height))),
                inside: rs.every(r => r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight),
                over: document.documentElement.scrollHeight - innerHeight,
                overX: document.documentElement.scrollWidth - innerWidth,
            };
        }""")
        assert dims["small"] >= 120, f"{label}: tiles are only {dims['small']}px — too small for small hands"
        assert dims["skew"] <= 2, f"{label}: tiles are {dims['skew']}px off square"
        assert dims["inside"], f"{label}: a tile hangs off the screen"
        assert dims["over"] <= 0 and dims["overX"] <= 0, f"{label}: the page scrolls ({dims})"

    check_fit("412x860")
    for w, h in [(393, 727), (1280, 800), (860, 412)]:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(300)
        check_fit(f"{w}x{h}")
    page.set_viewport_size({"width": 412, "height": 860})
    page.wait_for_timeout(300)
    ok("tiles are 120px+, square, and fit with no scrolling on phone and desktop sizes")

    # Spy on speech: the animals must not talk (a robotic TTS voice over the noises, #18).
    has_speech = page.evaluate("""() => {
        if (!('speechSynthesis' in window)) return false;
        window.__spoken = []; window.__cancels = 0;
        const speak = speechSynthesis.speak.bind(speechSynthesis);
        const cancel = speechSynthesis.cancel.bind(speechSynthesis);
        speechSynthesis.speak = u => { window.__spoken.push(u.text); };
        speechSynthesis.cancel = () => { window.__cancels++; cancel(); };
        return true;
    }""")

    expected = {"cow": "Moo!", "dog": "Woof woof!", "cat": "Meow!",
                "pig": "Oink oink!", "duck": "Quack quack!", "lion": "Roar!"}
    for animal in expected:
        tile = page.locator(f".ani-tile[data-animal={animal}]")
        assert tile.get_attribute("data-anim") is None, f"{animal} starts out mid-animation"
        box = tile.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        assert tile.get_attribute("data-anim") == "on", f"tapping the {animal} did not animate it"
        assert page.evaluate("document.getElementById('aniBoard').dataset.last") == animal, \
            f"the board does not know the {animal} was tapped"
    assert page.evaluate("document.getElementById('aniBoard').dataset.taps") == "6", "taps were not counted"
    page.wait_for_function("document.querySelectorAll('.ani-tile[data-anim]').length === 0", timeout=3000)
    ok("tapping each animal sets it bouncing, and it settles again")

    if has_speech:
        assert page.evaluate("window.__spoken") == [], "an animal spoke with the TTS voice"
        ok("the animals make their noises without a TTS voice on top")

    # Mashing: all six at once through separate pointers, then 60 more in a burst.
    taps_before = int(page.evaluate("document.getElementById('aniBoard').dataset.taps"))
    page.evaluate("""() => {
        [...document.querySelectorAll('.ani-tile')].forEach((t, i) => {
            const r = t.getBoundingClientRect();
            t.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 30 + i, pointerType: 'touch',
                bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
        });
    }""")
    assert page.locator(".ani-tile[data-anim=on]").count() == 6, "simultaneous touches did not all animate"
    for i in range(60):
        page.locator(".ani-tile").nth(i % 6).dispatch_event("pointerdown")
    taps_now = int(page.evaluate("document.getElementById('aniBoard').dataset.taps"))
    assert taps_now == taps_before + 66, f"mashing dropped taps: {taps_before} -> {taps_now}"
    assert "/games/animals/" in page.url, "mashing left the game"
    assert not errors, "JS errors in Animal Sounds: " + "; ".join(errors)
    ok("six fingers at once and a mashing burst: every tap counted, no JS errors")

    page.set_viewport_size(original_size)

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
