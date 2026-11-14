package com.trendwriter.app;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * WebView that runs the app from the APK's assets.
 * The page is served from https://appassets.androidplatform.net so it gets a stable
 * origin, which keeps localStorage (keys, drafts, history) across restarts and updates.
 * A small JS bridge does what a web page cannot: call the Naver APIs (no CORS),
 * save images to the gallery, copy rich text and open links in the browser.
 */
public class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final String START_URL = "https://" + HOST + "/assets/www/index.html";
    private static final int MAX_IMAGE_BYTES = 25 * 1024 * 1024;

    private WebView web;
    private final ExecutorService io = Executors.newCachedThreadPool();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        final WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .setDomain(HOST)
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();

        web = new WebView(this);
        web.setBackgroundColor(getColor(R.color.app_bg));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.addJavascriptInterface(new Bridge(), "TrendWriterNative");
        web.setWebChromeClient(new WebChromeClient()); // needed for confirm() dialogs
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return loader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Only the bundled page lives in this WebView; every other link opens in the browser.
                if (HOST.equals(request.getUrl().getHost())) return false;
                openInBrowser(request.getUrl().toString());
                return true;
            }
        });

        setContentView(web);
        web.loadUrl(START_URL);
    }

    private void openInBrowser(String url) {
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) return;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception ignored) {
            // No browser installed.
        }
    }

    /** Hands a result back to the page: window.__twNativeCb(id, status, body). */
    private void reply(final String id, final int status, final String body) {
        final String js = "window.__twNativeCb(" + JSONObject.quote(id) + "," + status + "," + JSONObject.quote(body == null ? "" : body) + ")";
        runOnUiThread(() -> {
            if (web != null) web.evaluateJavascript(js, null);
        });
    }

    private static byte[] readAll(InputStream in, int limit) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            if (out.size() > limit) throw new Exception("too large");
        }
        return out.toByteArray();
    }

    private void saveToGallery(byte[] bytes, String mime, String name) throws Exception {
        ContentValues v = new ContentValues();
        v.put(MediaStore.Images.Media.DISPLAY_NAME, name);
        v.put(MediaStore.Images.Media.MIME_TYPE, mime);
        v.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/TrendWriter");
        Uri uri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v);
        if (uri == null) throw new Exception("insert failed");
        try (OutputStream out = getContentResolver().openOutputStream(uri)) {
            if (out == null) throw new Exception("open failed");
            out.write(bytes);
        }
    }

    private final class Bridge {
        /** Naver API call. Only the two Naver API hosts and the paths the app uses are allowed. */
        @JavascriptInterface
        public void request(final String id, final String json) {
            io.execute(() -> {
                HttpURLConnection c = null;
                try {
                    JSONObject o = new JSONObject(json);
                    boolean hub = "hub".equals(o.optString("kind"));
                    String path = o.optString("path");
                    boolean allowed = hub
                            ? (path.startsWith("/search-trend/") || path.startsWith("/search/"))
                            : (path.startsWith("/v1/datalab/") || path.startsWith("/v1/search/"));
                    if (!allowed || path.contains("..")) {
                        reply(id, 404, "{\"errorMessage\":\"unknown api path\"}");
                        return;
                    }
                    String base = hub ? "https://naverapihub.apigw.ntruss.com" : "https://openapi.naver.com";
                    String method = "POST".equals(o.optString("method")) ? "POST" : "GET";
                    c = (HttpURLConnection) new URL(base + path).openConnection();
                    c.setConnectTimeout(15000);
                    c.setReadTimeout(30000);
                    c.setRequestMethod(method);
                    c.setRequestProperty(hub ? "X-NCP-APIGW-API-KEY-ID" : "X-Naver-Client-Id", o.optString("id"));
                    c.setRequestProperty(hub ? "X-NCP-APIGW-API-KEY" : "X-Naver-Client-Secret", o.optString("secret"));
                    String body = o.optString("body");
                    if ("POST".equals(method)) {
                        c.setDoOutput(true);
                        c.setRequestProperty("Content-Type", "application/json");
                        try (OutputStream out = c.getOutputStream()) {
                            out.write(body.getBytes(StandardCharsets.UTF_8));
                        }
                    }
                    int status = c.getResponseCode();
                    InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
                    String text = in == null ? "" : new String(readAll(in, 8 * 1024 * 1024), StandardCharsets.UTF_8);
                    reply(id, status, text);
                } catch (Exception e) {
                    reply(id, 0, "{\"errorMessage\":" + JSONObject.quote(String.valueOf(e.getMessage())) + "}");
                } finally {
                    if (c != null) c.disconnect();
                }
            });
        }

        /** Saves a generated image (base64) to Pictures/TrendWriter. */
        @JavascriptInterface
        public void saveImage(final String id, final String base64, final String mime, final String name) {
            io.execute(() -> {
                try {
                    saveToGallery(Base64.decode(base64, Base64.DEFAULT), mime, name);
                    reply(id, 200, "");
                } catch (Exception e) {
                    reply(id, 500, String.valueOf(e.getMessage()));
                }
            });
        }

        /** Downloads an image the user picked from the search results and saves it to the gallery. */
        @JavascriptInterface
        public void saveImageUrl(final String id, final String url, final String name) {
            io.execute(() -> {
                HttpURLConnection c = null;
                try {
                    if (!url.startsWith("https://") && !url.startsWith("http://")) throw new Exception("bad url");
                    c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(15000);
                    c.setReadTimeout(30000);
                    c.setRequestProperty("User-Agent", "Mozilla/5.0");
                    String type = c.getContentType();
                    if (c.getResponseCode() != 200 || type == null || !type.startsWith("image/")) throw new Exception("not an image");
                    int semi = type.indexOf(';');
                    saveToGallery(readAll(c.getInputStream(), MAX_IMAGE_BYTES), semi > 0 ? type.substring(0, semi) : type, name);
                    reply(id, 200, "");
                } catch (Exception e) {
                    reply(id, 500, String.valueOf(e.getMessage()));
                } finally {
                    if (c != null) c.disconnect();
                }
            });
        }

        /** Copies text, with an HTML version when given so bold subheadings survive the paste. */
        @JavascriptInterface
        public void copy(final String text, final String html) {
            runOnUiThread(() -> {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm == null) return;
                cm.setPrimaryClip(html == null || html.isEmpty()
                        ? ClipData.newPlainText("text", text)
                        : ClipData.newHtmlText("text", text, html));
            });
        }

        @JavascriptInterface
        public void open(final String url) {
            runOnUiThread(() -> openInBrowser(url));
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        // Back closes a dialog or returns to the previous step; on the first step it closes the app.
        if (web == null) { finish(); return; }
        web.evaluateJavascript(
                "(window.__trendWriter && window.__trendWriter.back()) ? 'y' : 'n'",
                value -> { if (!"\"y\"".equals(value)) finish(); });
    }

    @Override
    protected void onDestroy() {
        io.shutdownNow();
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
