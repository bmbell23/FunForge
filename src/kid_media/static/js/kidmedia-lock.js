/*
 * KidMedia — global parent lock.
 *
 * One unlock, everywhere, until you lock again. Previously each page kept its own
 * parent-mode flag in sessionStorage ('kidmedia_parent_mode' on the music pages,
 * 'kidmedia_podcast_parent_mode' on the podcast pages), so walking from Sounds to
 * Podcasts — or just reopening the app — asked for the PIN all over again, and the
 * home and video pages had no way to unlock at all.
 *
 * Now: one localStorage flag, one button rendered into every page's header, and a
 * touch keypad instead of window.prompt() (which a WebView can swallow entirely,
 * and which is miserable on a tablet anyway).
 *
 * The PIN is client-side by design. This keeps a three-year-old out of the delete
 * buttons; it is not a security boundary, and never was.
 */
(function () {
    'use strict';

    var KEY = 'kidmedia_unlocked';
    var EVENT = 'kidmedia:lockchange';

    function pin() {
        return String(window.KIDMEDIA_PIN || '1234');
    }

    function isUnlocked() {
        try {
            return localStorage.getItem(KEY) === 'true';
        } catch (e) {
            return false;
        }
    }

    function setUnlocked(on) {
        try {
            if (on) localStorage.setItem(KEY, 'true');
            else localStorage.removeItem(KEY);
        } catch (e) { /* private mode — fall back to in-page state only */ }
        applyBodyState();
        renderButton();
        document.dispatchEvent(new CustomEvent(EVENT, { detail: { unlocked: on } }));
    }

    function applyBodyState() {
        if (!document.body) return;
        document.body.classList.toggle('kid-unlocked', isUnlocked());
    }

    // ---- keypad -------------------------------------------------------------

    var pending = null;  // { onOk, onCancel }

    function showKeypad(onOk, onCancel) {
        closeKeypad();
        pending = { onOk: onOk, onCancel: onCancel };

        var overlay = document.createElement('div');
        overlay.className = 'pin-overlay';
        overlay.id = 'pinOverlay';
        overlay.innerHTML =
            '<div class="pin-panel" role="dialog" aria-label="Parent PIN">' +
              '<div class="pin-title">🔒 Parent PIN</div>' +
              '<div class="pin-dots" id="pinDots"></div>' +
              '<div class="pin-pad">' +
                [1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (n) {
                    return '<button class="pin-key" data-digit="' + n + '">' + n + '</button>';
                }).join('') +
                '<button class="pin-key pin-key-alt" data-action="clear">⌫</button>' +
                '<button class="pin-key" data-digit="0">0</button>' +
                '<button class="pin-key pin-key-alt" data-action="cancel">✕</button>' +
              '</div>' +
            '</div>';

        // Tapping the backdrop cancels; taps inside the panel must not.
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) cancel();
        });

        var entered = '';

        function paint(shake) {
            var dots = overlay.querySelector('#pinDots');
            dots.textContent = '';
            for (var i = 0; i < 4; i++) {
                var d = document.createElement('span');
                d.className = 'pin-dot' + (i < entered.length ? ' pin-dot-on' : '');
                dots.appendChild(d);
            }
            if (shake) {
                dots.classList.remove('pin-dots-bad');
                // Force a reflow so the animation restarts on a second wrong try.
                void dots.offsetWidth;
                dots.classList.add('pin-dots-bad');
            }
        }

        overlay.querySelector('.pin-pad').addEventListener('click', function (e) {
            var btn = e.target.closest('.pin-key');
            if (!btn) return;
            var action = btn.dataset.action;
            if (action === 'cancel') return cancel();
            if (action === 'clear') { entered = entered.slice(0, -1); return paint(); }

            entered += btn.dataset.digit;
            paint();
            if (entered.length < pin().length) return;

            if (entered === pin()) {
                var ok = pending && pending.onOk;
                pending = null;
                closeKeypad();
                if (ok) ok();
            } else {
                entered = '';
                paint(true);
            }
        });

        document.body.appendChild(overlay);
        paint();
        requestAnimationFrame(function () { overlay.classList.add('pin-overlay-visible'); });
    }

    function cancel() {
        var fn = pending && pending.onCancel;
        pending = null;
        closeKeypad();
        if (fn) fn();
    }

    function closeKeypad() {
        var el = document.getElementById('pinOverlay');
        if (el) el.remove();
    }

    // ---- public API ---------------------------------------------------------

    /**
     * Run `action` if we're unlocked; otherwise ask for the PIN first — and on
     * success unlock globally, so this is the LAST prompt until the parent locks
     * again. That is the whole point of the rewrite: one PIN entry, not one per
     * button press.
     */
    function require(action) {
        if (isUnlocked()) { if (action) action(); return; }
        showKeypad(function () {
            setUnlocked(true);
            if (action) action();
        });
    }

    function unlock() { require(null); }

    function lock() { setUnlocked(false); }

    function toggle() { if (isUnlocked()) lock(); else unlock(); }

    // ---- header button ------------------------------------------------------

    function renderButton() {
        var btn = document.getElementById('kidLockBtn');
        if (!btn) return;
        var on = isUnlocked();
        btn.textContent = on ? '🔓' : '🔒';
        btn.title = on ? 'Unlocked — tap to lock' : 'Locked — tap to unlock';
        btn.setAttribute('aria-label', btn.title);
        btn.classList.toggle('kid-lock-open', on);
    }

    /**
     * Mount the button. The music and podcast pages hide the shared header
     * (`body:has(.music-main) .header`), so prefer their own controls row and fall
     * back to the header everywhere else. Pages that want it somewhere specific
     * can provide an element with id="kidLockMount".
     */
    function mount() {
        if (document.getElementById('kidLockBtn')) return;
        var host = document.getElementById('kidLockMount')
            || document.querySelector('.controls-right')
            || document.querySelector('.header');
        if (!host) return;

        var btn = document.createElement('button');
        btn.id = 'kidLockBtn';
        btn.className = 'kid-lock-btn';
        btn.type = 'button';
        btn.addEventListener('click', toggle);
        host.appendChild(btn);
        renderButton();
    }

    window.KidLock = {
        isUnlocked: isUnlocked,
        require: require,
        unlock: unlock,
        lock: lock,
        toggle: toggle,
        onChange: function (fn) { document.addEventListener(EVENT, function (e) { fn(e.detail.unlocked); }); },
        EVENT: EVENT
    };

    document.addEventListener('DOMContentLoaded', function () {
        applyBodyState();
        mount();
    });

    // Another tab (or the remote page) locking should lock this one too.
    window.addEventListener('storage', function (e) {
        if (e.key !== KEY) return;
        applyBodyState();
        renderButton();
        document.dispatchEvent(new CustomEvent(EVENT, { detail: { unlocked: isUnlocked() } }));
    });
})();
