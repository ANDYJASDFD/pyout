package com.andy.pyout;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import java.net.HttpURLConnection;

/** PyOut build 3: output window for the runner in Termux (127.0.0.1:8765), with on-screen diagnostics. */
public class MainActivity extends Activity {
    private static final String URL = "http://127.0.0.1:8765/";
    private WebView web;
    private TextView status;
    private boolean failed = false;
    private String probeText = "testing...";
    private String webText = "loading...";
    private int progress = 0;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private void render() {
        status.setText("PyOut build 3\n\nConnecting to the runner...\n\n"
                + "app -> runner test:\n" + probeText + "\n\n"
                + "page: " + webText + " (" + progress + "%)");
    }

    private void probe() {
        new Thread(new Runnable() {
            @Override public void run() {
                long t0 = System.currentTimeMillis();
                String r;
                try {
                    HttpURLConnection c = (HttpURLConnection) new java.net.URL(URL + "files").openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(3000);
                    int code = c.getResponseCode();
                    c.disconnect();
                    r = "HTTP " + code + " in " + (System.currentTimeMillis() - t0) + " ms";
                } catch (Exception e) {
                    r = "FAILED: " + e;
                }
                final String res = r;
                runOnUiThread(new Runnable() {
                    @Override public void run() { probeText = res; render(); }
                });
            }
        }).start();
    }

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (status.getVisibility() == View.VISIBLE) {
                probe();
                handler.postDelayed(this, 3000);
            }
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
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
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);
        render();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMediaPlaybackRequiresUserGesture(false);

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView v, int p) {
                progress = p;
                render();
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                if (m.messageLevel() == ConsoleMessage.MessageLevel.ERROR) {
                    webText = "script error: " + m.message() + " line " + m.lineNumber();
                    render();
                }
                return true;
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView v, String url, Bitmap icon) {
                failed = false;
                webText = "started";
                render();
            }

            @Override
            public void onPageCommitVisible(WebView v, String url) {
                if (!failed) status.setVisibility(View.GONE);
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                webText = "finished";
                render();
                if (!failed) status.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) {
                    failed = true;
                    webText = "ERROR " + e.getErrorCode() + " " + e.getDescription();
                    render();
                    handler.postDelayed(new Runnable() {
                        @Override public void run() { web.loadUrl(URL); }
                    }, 2000);
                }
            }

            @Override
            public void onReceivedHttpError(WebView v, WebResourceRequest r, WebResourceResponse e) {
                if (r.isForMainFrame()) {
                    webText = "HTTP error " + e.getStatusCode();
                    render();
                }
            }
        });
        web.loadUrl(URL);
        probe();
        handler.postDelayed(tick, 3000);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }
}
