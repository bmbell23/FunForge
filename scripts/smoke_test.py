#!/usr/bin/env python3
"""End-to-end smoke test for KidMedia's player, parent lock and Android bridge.

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

# Stands in for the Android shell's window.KidMedia, recording every call so we
# can assert the page actually drives the foreground media service.
FAKE_BRIDGE = """
window.__bridgeCalls = [];
window.KidMedia = {
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
    page.evaluate("localStorage.setItem('kidmedia_unlocked','true')")
    go("/music/")
    assert page.evaluate("KidMediaNative.isNative"), "native bridge not detected"
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

    page.locator(".song-tile").nth(1).click()
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

    page.evaluate("closeMediaSheet(); clearQueue(); localStorage.removeItem('kidmedia_unlocked')")
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
