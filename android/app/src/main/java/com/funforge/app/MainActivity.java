package com.funforge.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * WebView shell around the FunForge server, plus:
 *  - a foreground {@link PlaybackService} so music keeps playing with the screen
 *    locked and shows up on the lock screen,
 *  - a self-updater that pulls a newer APK from /download/,
 *  - a crash reporter that paints the stack trace on screen instead of dying.
 */
public class MainActivity extends Activity {
    private static final String SERVER_URL = "http://100.69.184.113:8006";
    private static final String INSTALL_ACTION = "com.funforge.app.INSTALL_RESULT";
    private static final String APK_NAME = "funforge.apk";

    /** Rate-limit for the resume-time update check. */
    private static final long UPDATE_CHECK_INTERVAL_MS = 60_000L;
    private long lastUpdateCheckMs = 0L;

    private WebView webView;

    // Static so PlaybackService (same process) can route media-button and
    // notification actions back into the WebView's <audio> via JS.
    private static WebView sWebView;

    // The JsBridge holds the application context (it outlives any one activity),
    // so window flags have to go through the current activity instead.
    private static MainActivity sRef;

    /**
     * Called from PlaybackService's MediaSession callback. {@code action} is one of
     * play / pause / next / prev / seek:&lt;ms&gt;. Forwarded to window.__mediaControl,
     * which the web player installs and which drives the &lt;audio&gt; element.
     */
    static void dispatchMedia(final String action) {
        final WebView wv = sWebView;
        if (wv == null || action == null) return;
        // action is a fixed vocabulary (ascii + digits); single-quote safe.
        wv.post(() -> wv.evaluateJavascript(
            "window.__mediaControl && window.__mediaControl('" + action + "')", null));
    }

    private static void startMediaService(Context c, Intent i) {
        try {
            if (PlaybackService.ACTION_STOP.equals(i.getAction())) {
                c.startService(i);  // not foreground — the service stops itself
            } else if (Build.VERSION.SDK_INT >= 26) {
                c.startForegroundService(i);
            } else {
                c.startService(i);
            }
        } catch (Exception e) {
            android.util.Log.e("FunForge", "startMediaService failed", e);
        }
    }

    // Receives PackageInstaller session status. A non-privileged app must launch
    // the system confirmation UI itself when STATUS_PENDING_USER_ACTION comes back.
    private final BroadcastReceiver installReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, Integer.MIN_VALUE);
            if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm != null) {
                    confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(confirm);
                }
            } else if (status != PackageInstaller.STATUS_SUCCESS) {
                String msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                Toast.makeText(MainActivity.this, "Update failed: " + msg, Toast.LENGTH_LONG).show();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        sRef = this;

        // Catch ANY crash (including background threads) and show it on screen
        // instead of silently dying, so it can be screenshotted for debugging.
        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) ->
            runOnUiThread(() -> showError("Uncaught on " + thread.getName(), throwable)));

        // Android 13+ gates notifications — including the media notification that
        // backs the lock-screen controls — behind a runtime grant.
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{ android.Manifest.permission.POST_NOTIFICATIONS }, 1);
        }

        IntentFilter filter = new IntentFilter(INSTALL_ACTION);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(installReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(installReceiver, filter);
        }

        try {
            setupWebView(savedInstanceState);
        } catch (Throwable t) {
            showError("onCreate failed", t);
        }
    }

    private void setupWebView(Bundle savedInstanceState) {
        webView = new WebView(this);
        sWebView = webView;
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(false);
        settings.setSupportZoom(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        // Without this the first track never starts on its own: the queue is built
        // from a tap, but every later track (auto-advance, remote command, media
        // button) plays from a timer/callback with no user gesture attached.
        settings.setMediaPlaybackRequiresUserGesture(false);

        webView.addJavascriptInterface(new JsBridge(getApplicationContext()), "FunForge");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // Stay in-app for our server; open everything else externally.
                return !url.startsWith(SERVER_URL);
            }
        });

        // A bare WebChromeClient suppresses window.alert / confirm / prompt outright
        // (prompt returns null, confirm returns false, alert shows nothing), which
        // would silently swallow every confirmation in the admin screens.
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView view, String url, String message,
                                     final android.webkit.JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message,
                                       final android.webkit.JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView view, String url, String message,
                                      String defaultValue,
                                      final android.webkit.JsPromptResult result) {
                final android.widget.EditText input = new android.widget.EditText(MainActivity.this);
                input.setText(defaultValue);
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setView(input)
                        .setPositiveButton(android.R.string.ok,
                                (d, w) -> result.confirm(input.getText().toString()))
                        .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }
        });

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(SERVER_URL);
        }

        // Check for a newer APK a few seconds after the UI is up (off the critical path).
        webView.postDelayed(this::checkForUpdate, 4000);
    }

    // ---- JS bridge ----------------------------------------------------------

    /**
     * Exposed to the page as {@code window.FunForge}. The web player calls these to
     * drive the foreground service that keeps audio alive when the screen locks.
     */
    private static class JsBridge {
        private final Context app;
        private final android.os.Handler main =
            new android.os.Handler(android.os.Looper.getMainLooper());

        JsBridge(Context app) { this.app = app; }

        /** Lets the page detect the native shell (vs a plain browser tab). */
        @JavascriptInterface
        public boolean isNativeApp() { return true; }

        /** Begin/refresh the media session with this track's metadata. */
        @JavascriptInterface
        public void mediaStart(String title, String artist, String coverUrl) {
            Intent i = new Intent(app, PlaybackService.class)
                    .setAction(PlaybackService.ACTION_START);
            i.putExtra("title", title);
            i.putExtra("artist", artist);
            i.putExtra("coverUrl", coverUrl);
            i.putExtra("playing", true);
            startMediaService(app, i);
        }

        /**
         * Track changed while the service is already up. Done in-process when
         * possible so a locked screen can't block it (Android 12+ forbids
         * background foreground-service starts).
         */
        @JavascriptInterface
        public void mediaMeta(final String title, final String artist, final String coverUrl) {
            main.post(() -> {
                if (PlaybackService.isRunning()) {
                    PlaybackService.applyMetadata(title, artist, coverUrl);
                    return;
                }
                Intent i = new Intent(app, PlaybackService.class)
                        .setAction(PlaybackService.ACTION_START);
                i.putExtra("title", title);
                i.putExtra("artist", artist);
                i.putExtra("coverUrl", coverUrl);
                i.putExtra("playing", true);
                startMediaService(app, i);
            });
        }

        /** Push play/pause state, position and duration (both in seconds). */
        @JavascriptInterface
        public void mediaState(final boolean playing, final double position,
                               final double duration, final double rate) {
            main.post(() -> {
                if (PlaybackService.isRunning()) {
                    PlaybackService.applyState(playing, position, duration, rate);
                    return;
                }
                Intent i = new Intent(app, PlaybackService.class)
                        .setAction(PlaybackService.ACTION_UPDATE);
                i.putExtra("playing", playing);
                i.putExtra("position", position);
                i.putExtra("duration", duration);
                i.putExtra("rate", rate);
                startMediaService(app, i);
            });
        }

        /**
         * Keep the display awake while something is playing. FLAG_KEEP_SCREEN_ON is
         * the reliable path — the Web Wake Lock API silently no-ops in many Android
         * WebView configurations. Driven by the page rather than held for the whole
         * session so a phone left open on a paused player still sleeps normally;
         * background playback doesn't need it at all (that's PlaybackService's
         * PARTIAL_WAKE_LOCK).
         */
        @JavascriptInterface
        public void keepScreenOn(final boolean on) {
            main.post(() -> {
                MainActivity a = sRef;
                if (a == null || a.isFinishing()) return;
                if (on) {
                    a.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                } else {
                    a.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }

        /** Tear down the session + notification (queue cleared / player closed). */
        @JavascriptInterface
        public void mediaStop() {
            main.post(() -> {
                if (PlaybackService.isRunning()) { PlaybackService.stopFromBridge(); return; }
                Intent i = new Intent(app, PlaybackService.class)
                        .setAction(PlaybackService.ACTION_STOP);
                startMediaService(app, i);
            });
        }
    }

    // ---- Self-updater -------------------------------------------------------

    /** Fetch version.json; if it advertises a newer build, offer to install it. */
    private void checkForUpdate() {
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection)
                    new URL(SERVER_URL + "/download/version.json").openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(5000);
                if (c.getResponseCode() != 200) {
                    return;
                }
                StringBuilder sb = new StringBuilder();
                try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        sb.append(line);
                    }
                }
                int serverCode = new JSONObject(sb.toString()).getInt("versionCode");
                int myCode = getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
                if (serverCode > myCode) {
                    runOnUiThread(this::promptUpdate);
                }
            } catch (Exception ignored) {
                // Offline, VPN down, or no version.json yet — never bother the user.
            }
        }).start();
    }

    private void promptUpdate() {
        if (isFinishing()) {
            return;
        }
        new AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("A newer version of FunForge is ready. Install it now?")
            .setPositiveButton("Update", (d, w) -> startUpdate())
            .setNegativeButton("Later", null)
            .show();
    }

    private void startUpdate() {
        // Android 8+ requires the user to allow this app to install packages.
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(this)
                .setTitle("Allow installs")
                .setMessage("To auto-update, allow FunForge to install apps. You'll be sent to "
                    + "settings — turn it on, then reopen the app and tap Update again.")
                .setPositiveButton("Open settings", (d, w) -> startActivity(
                    new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + getPackageName()))))
                .setNegativeButton("Cancel", null)
                .show();
            return;
        }
        Toast.makeText(this, "Downloading update…", Toast.LENGTH_SHORT).show();
        new Thread(this::downloadAndInstall).start();
    }

    private void downloadAndInstall() {
        try {
            File apk = new File(getCacheDir(), "update.apk");
            HttpURLConnection c = (HttpURLConnection)
                new URL(SERVER_URL + "/download/" + APK_NAME).openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(20000);
            try (InputStream in = c.getInputStream();
                 OutputStream out = new java.io.FileOutputStream(apk)) {
                copy(in, out);
            }
            installApk(apk);
        } catch (Exception e) {
            runOnUiThread(() -> Toast.makeText(this,
                "Update failed: " + e.getMessage(), Toast.LENGTH_LONG).show());
        }
    }

    /** Stream the downloaded APK into a PackageInstaller session and commit it. */
    private void installApk(File apk) throws Exception {
        PackageInstaller installer = getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params =
            new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        int sessionId = installer.createSession(params);
        try (PackageInstaller.Session session = installer.openSession(sessionId)) {
            try (OutputStream out = session.openWrite("funforge", 0, apk.length());
                 InputStream in = new FileInputStream(apk)) {
                copy(in, out);
                session.fsync(out);
            }
            int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent pi = PendingIntent.getBroadcast(this, 0,
                new Intent(INSTALL_ACTION).setPackage(getPackageName()), flags);
            session.commit(pi.getIntentSender());
        }
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        byte[] buf = new byte[65536];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
    }

    // ---- Crash reporter -----------------------------------------------------

    /** Render an exception's stack trace on screen so it can be read/screenshotted. */
    private void showError(String label, Throwable t) {
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));

        TextView tv = new TextView(this);
        tv.setText("FunForge crashed:\n" + label + "\n\n" + sw);
        tv.setTextColor(Color.WHITE);
        tv.setBackgroundColor(Color.parseColor("#B00020"));
        tv.setPadding(40, 120, 40, 40);
        tv.setTextSize(12);
        tv.setTextIsSelectable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(tv);
        try {
            setContentView(scroll);
        } catch (Throwable ignored) {
            // window may be unusable; nothing more we can do
        }
    }

    // ---- Lifecycle ----------------------------------------------------------

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null) {
            webView.saveState(outState);
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        sRef = this;
        if (webView != null) {
            webView.onResume();
            // The WebView's JS timers are paused in onPause (below) only when
            // nothing is playing, so this is a no-op in that case.
            webView.resumeTimers();
        }
        maybeCheckForUpdate();
    }

    /** Update check, rate-limited so bouncing in and out doesn't hammer it. */
    private void maybeCheckForUpdate() {
        long now = System.currentTimeMillis();
        if (now - lastUpdateCheckMs < UPDATE_CHECK_INTERVAL_MS) {
            return;
        }
        lastUpdateCheckMs = now;
        checkForUpdate();
    }

    @Override
    protected void onPause() {
        super.onPause();
        // Deliberately NOT calling webView.onPause() while the media service is
        // running: onPause() suspends the WebView's media playback, which is
        // exactly what we're trying to prevent when the screen locks.
        if (webView != null && !PlaybackService.isRunning()) {
            webView.onPause();
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        try {
            unregisterReceiver(installReceiver);
        } catch (Exception ignored) {
        }
        if (sWebView == webView) {
            sWebView = null;
        }
        if (sRef == this) {
            sRef = null;
        }
        if (webView != null) {
            webView.destroy();
        }
    }
}
