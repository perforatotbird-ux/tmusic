package app.tmusic.mobile

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.webkit.JavascriptInterface
import com.getcapacitor.BridgeActivity
import org.json.JSONArray
import org.json.JSONObject

/**
 * JavascriptInterface exposed to WebView as `window.AndroidMediaBridge`.
 * Connects frontend playback events to the native MediaPlaybackService
 * and provides local network discovery capabilities for Wi-Fi environments.
 */
class AndroidMediaBridge(private val context: Context) {

    private val prefs = context.getSharedPreferences("tmusic_prefs", Context.MODE_PRIVATE)

    // --- Playback controls ---

    @JavascriptInterface
    fun setPlaybackState(state: String) {
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
        return LanServerScanner.getLocalIpv4Address(context) ?: ""
    }

    @JavascriptInterface
    fun getSavedServerUrl(): String {
        return prefs.getString("saved_server_url", "") ?: ""
    }

    @JavascriptInterface
    fun saveServerUrl(url: String) {
        prefs.edit().putString("saved_server_url", url).apply()
    }

    @JavascriptInterface
    fun connectToServer(url: String) {
        saveServerUrl(url)
        (context as? Activity)?.runOnUiThread {
            (context as? BridgeActivity)?.bridge?.webView?.loadUrl(url)
        }
    }

    @JavascriptInterface
    fun openLocalDiscovery() {
        (context as? Activity)?.runOnUiThread {
            (context as? BridgeActivity)?.bridge?.webView?.loadUrl("https://localhost/index.html")
        }
    }

    @JavascriptInterface
    fun startNetworkScan() {
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
}

