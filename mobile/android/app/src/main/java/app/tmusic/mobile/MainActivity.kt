package app.tmusic.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.webkit.WebView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.getcapacitor.BridgeActivity
import com.getcapacitor.WebViewListener
import java.lang.ref.WeakReference

/**
 * MainActivity for T-Music.
 * Inherits Capacitor's BridgeActivity and customizes:
 * - Status bar and navigation bar background matching #141210
 * - WebView settings: autoplay without gesture (Android 14 API 34), custom UserAgent
 * - AndroidMediaBridge for playback state, metadata, lockscreen sync and Wi-Fi LAN discovery
 * - Keep WebView timers active in background so audio playback does not stall
 * - POST_NOTIFICATIONS runtime permission on Android 13+
 * - Automatic fallback to local discovery screen upon connection loss
 */
class MainActivity : BridgeActivity() {

    companion object {
        private const val NOTIFICATION_PERMISSION_REQUEST_CODE = 2001
        private var instanceRef: WeakReference<MainActivity>? = null

        fun evaluateJs(script: String) {
            instanceRef?.get()?.runOnUiThread {
                instanceRef?.get()?.bridge?.webView?.evaluateJavascript(script, null)
            }
        }

        fun dispatchNativeCommand(action: String, details: Any? = null) {
            val script = if (details != null) {
                "window.__tmusicNativeCmd && window.__tmusicNativeCmd('$action', $details);"
            } else {
                "window.__tmusicNativeCmd && window.__tmusicNativeCmd('$action');"
            }
            evaluateJs(script)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instanceRef = WeakReference(this)

        setupThemeAndBars()
        setupWebView()
        requestNotificationPermissionIfNeeded()
    }

    private fun setupThemeAndBars() {
        val darkColor = Color.parseColor("#141210")
        window.statusBarColor = darkColor
        window.navigationBarColor = darkColor

        val insetsController = WindowInsetsControllerCompat(window, window.decorView)
        insetsController.isAppearanceLightStatusBars = false
        insetsController.isAppearanceLightNavigationBars = false
    }

    private fun setupWebView() {
        val webView = bridge.webView ?: return
        val settings = webView.settings

        // Android 14 (API 34): allow autoplay without manual touch gesture
        settings.mediaPlaybackRequiresUserGesture = false

        // User-Agent marker so frontend detects IS_NATIVE via 'TMusicApp/'
        val existingUa = settings.userAgentString ?: ""
        if (!existingUa.contains("TMusicApp/")) {
            settings.userAgentString = "$existingUa TMusicApp/1.0"
        }

        // Bridge for background playback, lockscreen control and Wi-Fi server discovery
        webView.addJavascriptInterface(AndroidMediaBridge(this), "AndroidMediaBridge")

        // Redirect to local discovery screen on connection failure
        bridge.addWebViewListener(object : WebViewListener() {
            override fun onReceivedError(wv: WebView?) {
                val currentUrl = wv?.url ?: ""
                if (!currentUrl.contains("localhost")) {
                    wv?.post {
                        wv.loadUrl("https://localhost/index.html")
                    }
                }
            }
        })
    }

    override fun onPause() {
        super.onPause()
        // Prevent WebView from freezing audio playback timers when app is minimized
        bridge.webView?.resumeTimers()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val action = intent.action
        if (!action.isNullOrBlank()) {
            when (action) {
                MediaPlaybackService.ACTION_CMD_PLAY -> dispatchNativeCommand("play")
                MediaPlaybackService.ACTION_CMD_PAUSE -> dispatchNativeCommand("pause")
                MediaPlaybackService.ACTION_CMD_TOGGLE -> dispatchNativeCommand("playpause")
                MediaPlaybackService.ACTION_CMD_NEXT -> dispatchNativeCommand("next")
                MediaPlaybackService.ACTION_CMD_PREV -> dispatchNativeCommand("previous")
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    NOTIFICATION_PERMISSION_REQUEST_CODE
                )
            }
        }
    }

    override fun onDestroy() {
        if (instanceRef?.get() == this) {
            instanceRef = null
        }
        super.onDestroy()
    }
}
