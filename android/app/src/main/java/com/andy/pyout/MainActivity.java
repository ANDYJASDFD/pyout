package com.andy.pyout;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.content.Intent;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

/** PyOut: output window for the runner in Termux (127.0.0.1:8765). Also lends the phone camera to your scripts. */
public class MainActivity extends Activity {
    private static final String URL = "http://127.0.0.1:8765/";
    private WebView web;
    private TextView status;
    private boolean failed = false;
    private PermissionRequest pendingCam;
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        startRunner();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0d1117"));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d1117"));
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(16);
        status.setPadding(40, 40, 40, 40);
        status.setGravity(Gravity.CENTER);
        status.setBackgroundColor(Color.parseColor("#0d1117"));
        status.setText("PyOut\n\nConnecting to the runner...\n\nIn Termux (Ubuntu) run:\npython ~/pyout/termux/pyout.py serve");
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMediaPlaybackRequiresUserGesture(false);

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                            request.grant(request.getResources());
                        } else {
                            pendingCam = request;
                            requestPermissions(new String[]{Manifest.permission.CAMERA}, 7);
                        }
                    }
                });
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                return true;
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView v, String url, Bitmap icon) {
                failed = false;
            }

            @Override
            public void onPageCommitVisible(WebView v, String url) {
                if (!failed) status.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) {
                    failed = true;
                    status.setVisibility(View.VISIBLE);
                    status.setText("PyOut runner not found.\n\nIn Termux (Ubuntu) run:\npython ~/pyout/termux/pyout.py serve\n\n(" + e.getDescription() + ")\n\nTrying again every 2 seconds...");
                    handler.postDelayed(new Runnable() {
                        @Override public void run() { web.loadUrl(URL); }
                    }, 2000);
                }
            }
        });
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface public void start() {
                runOnUiThread(new Runnable() { @Override public void run() { startRunner(); } });
            }
        }, "PyOutApp");
        web.loadUrl(URL);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code == 9) { startRunner(); return; }
        if (pendingCam != null) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                pendingCam.grant(pendingCam.getResources());
            } else {
                pendingCam.deny();
            }
            pendingCam = null;
        }
    }

    /** Asks Termux to run ~/.pyout_start.sh (starts the runner if it is not running yet). */
    private void startRunner() {
        try {
            if (checkSelfPermission("com.termux.permission.RUN_COMMAND") != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{"com.termux.permission.RUN_COMMAND"}, 9);
                return;
            }
            Intent i = new Intent("com.termux.RUN_COMMAND");
            i.setClassName("com.termux", "com.termux.app.RunCommandService");
            i.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/home/.pyout_start.sh");
            i.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
            i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            if (android.os.Build.VERSION.SDK_INT >= 26) startForegroundService(i); else startService(i);
        } catch (Throwable e) {
            // Termux missing or not allowing it: the normal "connecting" screen still works
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        startRunner();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }
}
