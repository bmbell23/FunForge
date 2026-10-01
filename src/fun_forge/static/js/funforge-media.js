/*
 * FunForge — OS media integration.
 *
 * One place that knows how to tell the outside world what is playing:
 *
 *  - navigator.mediaSession, for browsers (lock screen, Bluetooth remotes,
 *    smartwatch controls via the paired phone).
 *  - window.FunForge, the native bridge in the Android wrapper. It runs a
 *    foreground service holding a MediaSession + a PARTIAL_WAKE_LOCK, which is
 *    what actually keeps audio alive once the screen locks. A plain <audio> in a
 *    WebView gets frozen with the process a few minutes after the screen goes
 *    off, and mediaSession alone does not prevent that.
 *
 * Pages hand it their <audio> element plus callbacks and otherwise forget about
 * it. Hardware buttons, notification buttons and lock-screen controls all arrive
 * back through the same callbacks the on-screen buttons use.
 */
(function () {
    'use strict';

    var native = null;
    try {
        // The bridge is only injected by the Android shell; isNativeApp() also
        // guards against a stale/partial interface after an app update.
        //
        // window.KidMedia is the pre-rename name of the same interface. The
        // rename changed the Android applicationId, so the old KidMedia APK
        // cannot self-update into FunForge — it has to be replaced by hand, and
        // until that happens it is still pointed at this server. Without this
        // fallback its lock-screen controls and background playback would break
        // the moment this file shipped. Delete it once the old app is gone.
        var bridge = window.FunForge || window.KidMedia;
        if (bridge && typeof bridge.isNativeApp === 'function'
            && bridge.isNativeApp()) {
            native = bridge;
        }
    } catch (e) { native = null; }

    var cfg = null;
    var sessionStarted = false;
    var lastMetaKey = '';
    var pushTimer = null;

    function absolute(url) {
        if (!url) return '';
        try {
            // PlaybackService fetches the cover with java.net.URL, which needs an
            // absolute address — the page's own relative /static/covers/… won't do.
            return new URL(url, window.location.origin).href;
        } catch (e) {
            return '';
        }
    }

    function meta() {
        var m = null;
        try { m = cfg && cfg.getMeta && cfg.getMeta(); } catch (e) { m = null; }
        if (!m) return null;
        return {
            title: m.title || '',
            artist: m.artist || '',
            album: m.album || 'FunForge',
            cover: absolute(m.cover)
        };
    }

    function audioEl() { return cfg && cfg.audio; }

    // ---- outbound: tell the OS what's playing -------------------------------

    function publishMetadata(force) {
        var m = meta();
        if (!m) return;
        var key = m.title + '\u0000' + m.artist + '\u0000' + m.cover;
        if (!force && key === lastMetaKey) return;
        lastMetaKey = key;

        if ('mediaSession' in navigator && window.MediaMetadata) {
            try {
                navigator.mediaSession.metadata = new MediaMetadata({
                    title: m.title,
                    artist: m.artist,
                    album: m.album,
                    artwork: m.cover
                        ? [{ src: m.cover, sizes: '512x512', type: 'image/jpeg' }]
                        : []
                });
            } catch (e) { /* not fatal — controls still work without art */ }
        }

        if (!native) return;
        try {
            if (!sessionStarted) {
                native.mediaStart(m.title, m.artist, m.cover);
                sessionStarted = true;
            } else {
                native.mediaMeta(m.title, m.artist, m.cover);
            }
        } catch (e) { /* bridge went away (app update) — degrade to web-only */ }
    }

    function publishState() {
        var a = audioEl();
        if (!a) return;
        var playing = !a.paused && !a.ended;

        if ('mediaSession' in navigator) {
            navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
        }

        if (!native) return;
        // A track can start playing before any metadata push (first item in a
        // queue), and the service needs to exist before it can be updated.
        if (!sessionStarted && playing) publishMetadata(true);
        try {
            native.mediaState(playing,
                isFinite(a.currentTime) ? a.currentTime : 0,
                isFinite(a.duration) ? a.duration : 0,
                a.playbackRate || 1);
            // Hold the display awake only while something is actually playing —
            // a phone parked on a paused player should still go to sleep.
            if (typeof native.keepScreenOn === 'function') native.keepScreenOn(playing);
        } catch (e) { /* ignore */ }
    }

    /** Track changed — refresh metadata and state together. */
    function trackChanged() {
        publishMetadata(true);
        publishState();
    }

    /** Playback is over for good: drop the notification and the wake lock. */
    function endSession() {
        sessionStarted = false;
        lastMetaKey = '';
        if ('mediaSession' in navigator) {
            navigator.mediaSession.playbackState = 'none';
            navigator.mediaSession.metadata = null;
        }
        if (!native) return;
        try {
            native.mediaStop();
            if (typeof native.keepScreenOn === 'function') native.keepScreenOn(false);
        } catch (e) { /* ignore */ }
    }

    // ---- inbound: OS/hardware controls --------------------------------------

    function call(name, arg) {
        var fn = cfg && cfg[name];
        if (typeof fn === 'function') fn(arg);
    }

    /**
     * Called from the Android shell (MainActivity.dispatchMedia) for media-button
     * and notification-button presses. Kept on `window` because the native side
     * evaluates it by name.
     */
    window.__mediaControl = function (action) {
        if (!cfg) return;
        if (action === 'play') return call('onPlay');
        if (action === 'pause') return call('onPause');
        if (action === 'next') return call('onNext');
        if (action === 'prev') return call('onPrev');
        if (action === 'stop') return call('onStop');
        if (action.indexOf('seek:') === 0) {
            var ms = parseFloat(action.slice(5));
            if (isFinite(ms)) call('onSeek', ms / 1000);
        }
    };

    function wireMediaSession() {
        if (!('mediaSession' in navigator)) return;
        var handlers = {
            play: function () { call('onPlay'); },
            pause: function () { call('onPause'); },
            nexttrack: function () { call('onNext'); },
            previoustrack: function () { call('onPrev'); },
            stop: function () { call('onStop'); }
        };
        Object.keys(handlers).forEach(function (name) {
            // Unsupported actions throw rather than no-op in some browsers.
            try { navigator.mediaSession.setActionHandler(name, handlers[name]); } catch (e) {}
        });
    }

    // ---- setup --------------------------------------------------------------

    /**
     * @param options.audio     the <audio> element driving playback
     * @param options.getMeta   () => {title, artist, album, cover} for the current item
     * @param options.onPlay/onPause/onNext/onPrev/onStop/onSeek  control callbacks
     */
    function attach(options) {
        cfg = options || {};
        var a = audioEl();
        if (!a) return;

        wireMediaSession();

        a.addEventListener('play', function () { publishMetadata(true); publishState(); startPushLoop(); });
        a.addEventListener('playing', publishState);
        a.addEventListener('pause', function () { publishState(); stopPushLoop(); });
        a.addEventListener('ended', publishState);
        a.addEventListener('loadedmetadata', publishState);
        a.addEventListener('ratechange', publishState);

        // The notification's progress bar and the lock-screen scrubber read the
        // position we last pushed, so refresh it while playing. 5s is frequent
        // enough to look live without waking the radio for nothing.
        if (!a.paused) startPushLoop();
    }

    function startPushLoop() {
        if (pushTimer) return;
        pushTimer = setInterval(publishState, 5000);
    }

    function stopPushLoop() {
        if (!pushTimer) return;
        clearInterval(pushTimer);
        pushTimer = null;
    }

    window.FunForgeNative = {
        isNative: !!native,
        attach: attach,
        trackChanged: trackChanged,
        publishState: publishState,
        endSession: endSession
    };
})();
