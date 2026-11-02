package app.tmusic.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Looper
import android.webkit.JavascriptInterface
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import com.getcapacitor.BridgeActivity
import org.json.JSONArray
import org.json.JSONObject

/**
 * JavascriptInterface exposed to WebView as `window.AndroidMediaBridge`.
 * Connects frontend playback events to the native MediaPlaybackService
 * and provides local network discovery capabilities for Wi-Fi environments.
 *
 * 2026-11-02 audit (M-4): addJavascriptInterface exposes the bridge to EVERY
 * page the WebView ever shows (including any site a server page links or
 * redirects to). Each method now checks the origin of the current page and
 * only serves the bundled launcher (https://localhost) and the origin of the
 * saved T-Music server. Server URLs are validated (http/https only, a real
 * host, no user-info) before they are stored or loaded.
 */
class AndroidMediaBridge(private val context: Context) {

    private val prefs = context.getSharedPreferences("tmusic_prefs", Context.MODE_PRIVATE)

    // --- Caller origin / URL validation (audit M-4) ---

    private fun currentPageUrl(): String? {
        val webView = (context as? BridgeActivity)?.bridge?.webView ?: return null
        if (Looper.myLooper() == Looper.getMainLooper()) return webView.url
        val result = AtomicReference<String?>(null)
        val latch = CountDownLatch(1)
        (context as? Activity)?.runOnUiThread {
            try {
                result.set(webView.url)
            } finally {
                latch.countDown()
            }
        } ?: return null
        return if (latch.await(500, TimeUnit.MILLISECONDS)) result.get() else null
    }

    private fun originOf(url: String?): String? {
        if (url.isNullOrBlank()) return null
        return try {
            val uri = Uri.parse(url)
            val scheme = uri.scheme?.lowercase() ?: return null
            val host = uri.host?.lowercase() ?: return null
            val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80
            "$scheme://$host:$port"
        } catch (e: Exception) {
            null
        }
    }

    private fun callerTrusted(): Boolean {
        val caller = originOf(currentPageUrl()) ?: return false
        if (caller == originOf(LOCAL_LAUNCHER_URL)) return true
        val saved = originOf(prefs.getString("saved_server_url", "") ?: "")
        return saved != null && caller == saved
    }

    private fun isValidServerUrl(url: String): Boolean {
        if (url.length > 2048) return false
        return try {
            val uri = Uri.parse(url.trim())
            val scheme = uri.scheme?.lowercase()
            (scheme == "http" || scheme == "https") &&
                !uri.host.isNullOrBlank() &&
                uri.userInfo.isNullOrEmpty()
        } catch (e: Exception) {
            false
        }
    }

    // --- Playback controls ---

    @JavascriptInterface
    fun setPlaybackState(state: String) {
        if (!callerTrusted()) return
        val intent = Intent(context, MediaPlaybackService::class.java).apply {
            action = MediaPlaybackService.ACTION_SET_PLAYBACK_STATE
            putExtra(MediaPlaybackService.EXTRA_PLAYBACK_STATE, state)
        }
        try {
            if (state == "playing") {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    @JavascriptInterface
    fun updateMetadata(title: String, artist: String, album: String, coverUrl: String, durationSec: Double) {
        if (!callerTrusted()) return
        val intent = Intent(context, MediaPlaybackService::class.java).apply {
            action = MediaPlaybackService.ACTION_UPDATE_METADATA
            putExtra(MediaPlaybackService.EXTRA_TITLE, title)
            putExtra(MediaPlaybackService.EXTRA_ARTIST, artist)
            putExtra(MediaPlaybackService.EXTRA_ALBUM, album)
            putExtra(MediaPlaybackService.EXTRA_COVER_URL, coverUrl)
            putExtra(MediaPlaybackService.EXTRA_DURATION, (durationSec * 1000).toLong())
        }
        try {
            context.startService(intent)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    @JavascriptInterface
    fun updatePosition(durationSec: Double, positionSec: Double) {
        if (!callerTrusted()) return
        val intent = Intent(context, MediaPlaybackService::class.java).apply {
            action = MediaPlaybackService.ACTION_UPDATE_POSITION
            putExtra(MediaPlaybackService.EXTRA_DURATION, (durationSec * 1000).toLong())
            putExtra(MediaPlaybackService.EXTRA_POSITION, (positionSec * 1000).toLong())
        }
        try {
            context.startService(intent)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    // --- LAN Discovery & Server Management ---

    @JavascriptInterface
    fun getLocalIp(): String {
        if (!callerTrusted()) return ""
        return LanServerScanner.getLocalIpv4Address(context) ?: ""
    }

    @JavascriptInterface
    fun getSavedServerUrl(): String {
        if (!callerTrusted()) return ""
        return prefs.getString("saved_server_url", "") ?: ""
    }

    @JavascriptInterface
    fun saveServerUrl(url: String) {
        if (!callerTrusted()) return
        if (!isValidServerUrl(url)) return
        prefs.edit().putString("saved_server_url", url.trim()).apply()
    }

    @JavascriptInterface
    fun connectToServer(url: String) {
        if (!callerTrusted()) return
        val target = url.trim()
        if (!isValidServerUrl(target)) return
        prefs.edit().putString("saved_server_url", target).apply()
        (context as? Activity)?.runOnUiThread {
            (context as? BridgeActivity)?.bridge?.webView?.loadUrl(target)
        }
    }

    @JavascriptInterface
    fun openLocalDiscovery() {
        if (!callerTrusted()) return
        (context as? Activity)?.runOnUiThread {
            (context as? BridgeActivity)?.bridge?.webView?.loadUrl(LOCAL_LAUNCHER_URL)
        }
    }

    @JavascriptInterface
    fun startNetworkScan() {
        if (!callerTrusted()) return
        LanServerScanner.startScan(
            context,
            onServerFound = { server ->
                val serverJsonStr = JSONObject().apply {
                    put("name", server.name)
                    put("url", server.url)
                    put("ip", server.ip)
                    put("port", server.port)
                    put("method", server.method)
                }.toString()
                MainActivity.evaluateJs("window.__onServerFound && window.__onServerFound($serverJsonStr);")
            },
            onComplete = { allServers ->
                val array = JSONArray()
                allServers.forEach { server ->
                    array.put(JSONObject().apply {
                        put("name", server.name)
                        put("url", server.url)
                        put("ip", server.ip)
                        put("port", server.port)
                        put("method", server.method)
                    })
                }
                MainActivity.evaluateJs("window.__onScanComplete && window.__onScanComplete($array);")
            }
        )
    }

    // --- Device Storage Info for Telegram-like Cache Settings ---

    @JavascriptInterface
    fun getDeviceStorageInfo(): String {
        if (!callerTrusted()) return "{}"
        return try {
            val stat = android.os.StatFs(context.filesDir.path)
            val blockSize = stat.blockSizeLong
            val totalBlocks = stat.blockCountLong
            val availableBlocks = stat.availableBlocksLong
            val totalBytes = totalBlocks * blockSize
            val availableBytes = availableBlocks * blockSize

            fun dirSize(dir: java.io.File): Long {
                var size = 0L
                dir.listFiles()?.forEach { file ->
                    size += if (file.isDirectory) dirSize(file) else file.length()
                }
                return size
            }
            val appCacheBytes = dirSize(context.cacheDir)

            val json = JSONObject().apply {
                put("totalBytes", totalBytes)
                put("availableBytes", availableBytes)
                put("appCacheBytes", appCacheBytes)
            }
            json.toString()
        } catch (e: Exception) {
            "{}"
        }
    }

    companion object {
        private const val LOCAL_LAUNCHER_URL = "https://localhost/index.html"
    }
}
