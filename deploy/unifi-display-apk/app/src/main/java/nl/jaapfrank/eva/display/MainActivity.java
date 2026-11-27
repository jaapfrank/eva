package nl.jaapfrank.eva.display;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;

import java.util.regex.Pattern;

/**
 * Full-screen UniFi Connect Display host for 3D Eva (`UC-Display-SE-21`, Android 9 arm64-v8a).
 *
 * Unlocks:
 * 1. Built-in Microphone & Speakers for WebRTC (`gpt-realtime-2.1` / `gemini-3.8-live`) via
 *    automatic `PermissionRequest.grant()` + `AudioManager.MODE_IN_COMMUNICATION` hardware AEC.
 * 2. Local RFC1918 LAN HTTPS (`:8766`) self-signed TLS trust so Android WebView exposes
 *    `window.isSecureContext === true` & `navigator.mediaDevices.getUserMedia` on home LAN,
 *    while keeping strict TLS verification for all non-LAN internet hosts.
 * 3. 5-tap top-left corner setup dialog so Eva's IP/URL can be updated anytime on the wall.
 */
public final class MainActivity extends Activity {

    private static final String PREFS_NAME = "eva_unifi_display_prefs";
    private static final String KEY_STAGE_URL = "stage_https_url";
    private static final Pattern PRIVATE_LAN_HOST = Pattern.compile(
            "^(localhost|127\\.\\d+\\.\\d+\\.\\d+|10\\.\\d+\\.\\d+\\.\\d+|192\\.168\\.\\d+\\.\\d+|172\\.(1[6-9]|2\\d|3[0-1])\\.\\d+\\.\\d+|[a-zA-Z0-9_-]+\\.local)$"
    );

    private WebView webView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private int cornerTapCount = 0;
    private long lastCornerTapMs = 0L;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                        | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
                        | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
        );
        enterImmersiveFullscreen();
        enableHardwareEchoCancellation();
        ensureMicrophoneRuntimePermission();

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#050914"));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#050914"));
        configureWebView(webView);
        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        // Invisible 96x96px 5-tap corner trigger (top-left) to edit Eva URL anytime on the wall
        View cornerHotspot = new View(this);
        cornerHotspot.setBackgroundColor(Color.TRANSPARENT);
        cornerHotspot.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                long now = System.currentTimeMillis();
                if (now - lastCornerTapMs > 2200L) {
                    cornerTapCount = 0;
                }
                lastCornerTapMs = now;
                cornerTapCount++;
                if (cornerTapCount >= 5) {
                    cornerTapCount = 0;
                    showUrlSetupDialog(false);
                }
            }
            return false;
        });
        FrameLayout.LayoutParams hotspotLp = new FrameLayout.LayoutParams(110, 110, Gravity.TOP | Gravity.START);
        root.addView(cornerHotspot, hotspotLp);

        setContentView(root);

        String savedUrl = getSavedStageUrl();
        if (savedUrl.isEmpty()) {
            showUrlSetupDialog(true);
        } else {
            webView.loadUrl(savedUrl);
        }
    }

    private void enableHardwareEchoCancellation() {
        try {
            AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            if (am != null) {
                am.setMode(AudioManager.MODE_IN_COMMUNICATION);
                am.setSpeakerphoneOn(true);
            }
        } catch (Exception ignored) {
        }
    }

    private void ensureMicrophoneRuntimePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1001);
            }
        }
    }

    private void configureWebView(WebView wv) {
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(wv, true);

        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUserAgentString(s.getUserAgentString() + " EvaUnifiDisplay/1.0");

        wv.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    try {
                        request.grant(request.getResources());
                    } catch (Exception ignored) {
                    }
                });
            }
        });

        wv.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                // Accept Eva's self-signed local CA ONLY on private RFC1918 home LAN hosts
                String url = error != null ? error.getUrl() : "";
                String host = "";
                try {
                    Uri parsed = Uri.parse(url);
                    host = parsed.getHost() != null ? parsed.getHost() : "";
                } catch (Exception ignored) {
                }
                if (!host.isEmpty() && PRIVATE_LAN_HOST.matcher(host).matches()) {
                    handler.proceed();
                } else {
                    handler.cancel();
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                CookieManager.getInstance().flush();
                // Once paired on /pair?kiosk=1#EVA-DISP-..., persist clean /?kiosk=1 URL without one-time hash
                if (url != null && url.contains("?kiosk=1") && !url.contains("/pair")) {
                    saveStageUrl(url);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    mainHandler.postDelayed(() -> {
                        String target = getSavedStageUrl();
                        if (!target.isEmpty() && webView != null) {
                            webView.loadUrl(target);
                        }
                    }, 6000L);
                }
            }
        });
    }

    private String getSavedStageUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        return prefs.getString(KEY_STAGE_URL, "").trim();
    }

    private void saveStageUrl(String url) {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit().putString(KEY_STAGE_URL, url.trim()).apply();
    }

    private void showUrlSetupDialog(boolean firstRun) {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        String current = getSavedStageUrl();
        input.setText(current.isEmpty() ? "https://10.0.20.166:8766/pair?kiosk=1#EVA-DISP-" : current);
        input.setSelection(input.getText().length());

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle("Eva Hoofdscherm Koppelen")
                .setMessage("Vul de HTTPS Hoofdscherm-URL in die rechts bij '🖥️ Hoofdscherm' op je Eva-scherm staat (tik later 5× linksboven om dit adres aan te passen):")
                .setView(input)
                .setCancelable(!firstRun)
                .setPositiveButton("Verbind met Eva", (dialog, which) -> {
                    String val = input.getText().toString().trim();
                    if (!val.isEmpty()) {
                        saveStageUrl(val);
                        webView.loadUrl(val);
                    }
                    enterImmersiveFullscreen();
                });
        if (!firstRun) {
            builder.setNeutralButton("Herlaad nu", (dialog, which) -> {
                webView.reload();
                enterImmersiveFullscreen();
            });
        }
        builder.show();
    }

    private void enterImmersiveFullscreen() {
        View decor = getWindow().getDecorView();
        decor.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
        );
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            enterImmersiveFullscreen();
        }
    }
}
