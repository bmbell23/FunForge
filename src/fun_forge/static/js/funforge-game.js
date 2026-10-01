/**
 * FunForge game shell.
 *
 * Every game gets the same three things from this file, so no individual game
 * has to remember them:
 *
 *   1. A locked exit. Starting a game is a commitment — you cannot back out and
 *      change your mind. Without that a three-year-old spends the whole session
 *      flipping between games instead of playing one. Leaving costs a PIN, via
 *      the same KidLock every other grown-up action in the app uses.
 *
 *   2. A back button that actually stays shut. MainActivity.onKeyDown runs
 *      webView.goBack() whenever history allows, and it cannot see anything this
 *      page believes about being locked — so a hardware Back would drop the kid
 *      at the picker in one tap on the most obvious button on the device. We push
 *      a sentinel history entry and re-push it on every popstate, which turns
 *      Back into a no-op without touching the Java. (Keeping a history entry
 *      matters for the opposite reason too: with none, canGoBack() is false, Back
 *      falls through to super.onKeyDown, and the whole app closes.)
 *
 *   3. Pause when the game is not on screen, so nothing animates or makes noise
 *      into a pocket.
 *
 * Games and the media player are separate worlds: arriving here has already
 * destroyed the queue's <audio>, so a game owns audio outright and never has to
 * duck or share.
 */
(function () {
    'use strict';

    var STATE = { ffGameLock: 1 };
    var locked = true;
    var pauseFns = [];
    var resumeFns = [];
    var nudgeTimer = null;

    // ---- back button --------------------------------------------------------

    function trapBack() {
        try {
            history.pushState(STATE, '');
        } catch (e) {
            return;             // no history API: the exit button still works
        }
        window.addEventListener('popstate', function () {
            if (!locked) return;
            try { history.pushState(STATE, ''); } catch (e) { /* nothing to do */ }
            nudge();
        });
    }

    /** Tell whoever pressed Back why nothing happened, without scolding a kid. */
    function nudge() {
        var el = document.getElementById('gameLockNudge');
        if (!el) return;
        el.hidden = false;
        el.classList.add('game-nudge-visible');
        clearTimeout(nudgeTimer);
        nudgeTimer = setTimeout(function () {
            el.classList.remove('game-nudge-visible');
        }, 2200);
    }

    // ---- leaving ------------------------------------------------------------

    /* If the app was locked when this game started, put it back that way on the
       way out. KidLock.require() unlocks globally on success — the app's usual
       bargain, one PIN entry rather than one per button — but here that would
       quietly disarm the feature: a parent who PINs a kid out of one game would
       leave every later game freely exitable until somebody thought to re-lock.
       A parent who was already unlocked stays unlocked, which is what they'd
       expect. */
    var startedLocked = true;

    function leave() {
        locked = false;         // let the popstate handler stop re-pushing
        if (startedLocked && window.KidLock) {
            try { window.KidLock.lock(); } catch (e) { /* leaving still wins */ }
        }
        window.location.href = '/games/';
    }

    /** Ask for the PIN, then go. KidLock unlocks globally on success, which is
     *  the app's established bargain: one PIN entry, not one per button. */
    function requestExit() {
        if (window.KidLock) {
            window.KidLock.require(leave);
        } else {
            leave();            // lock script missing: don't trap anyone in here
        }
    }

    // ---- visibility ---------------------------------------------------------

    function onVisibility() {
        var fns = document.hidden ? pauseFns : resumeFns;
        for (var i = 0; i < fns.length; i++) {
            try { fns[i](); } catch (e) { /* one bad handler shouldn't stop the rest */ }
        }
    }

    // ---- boot ---------------------------------------------------------------

    function mount() {
        startedLocked = !(window.KidLock && window.KidLock.isUnlocked());
        var btn = document.getElementById('gameExitBtn');
        if (btn) btn.addEventListener('click', requestExit);
        document.addEventListener('visibilitychange', onVisibility);
        trapBack();
    }

    window.FunForgeGame = {
        onPause: function (fn) { pauseFns.push(fn); },
        onResume: function (fn) { resumeFns.push(fn); },
        exit: requestExit,
        isLocked: function () { return locked; }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
