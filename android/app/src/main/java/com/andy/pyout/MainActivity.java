package com.andy.pyout;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** PyOut: the output window. All it does is show the runner that lives in Termux (127.0.0.1:8765). */
public class MainActivity extends Activity {
    private static final String URL = "http://127.0.0.1:8765/";
    private WebView web;

    private static final String ERROR_PAGE =
        "<html><body style='background:#0d1117;color:#e6edf3;font-family:sans-serif;padding:24px'>"
        + "<h2>PyOut runner not found</h2>"
        + "<p>Open Termux and start it:</p>"
        + "<pre style='background:#010409;padding:12px;border-radius:8px'>source ~/tfenv/bin/activate\n"
        + "python pyout.py serve</pre>"
        + "<p>Then come back and press Retry.</p>"
        + "<button style='font-size:18px;padding:12px 24px' onclick=\"location.href='" + URL + "'\">Retry</button>"
        + "</body></html>";

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d1117"));
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) v.loadDataWithBaseURL(URL, ERROR_PAGE, "text/html", "utf-8", null);
            }
        });
        web.loadUrl(URL);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null && web.getUrl() == null) web.loadUrl(URL);
    }
}
