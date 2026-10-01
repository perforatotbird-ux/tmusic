package app.tmusic.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.support.v4.media.MediaMetadataCompat
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import androidx.core.app.NotificationCompat
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Foreground service with type `mediaPlayback` (Android 14 / API 34).
 * Handles:
 * - MediaSessionCompat (lock screen, bluetooth headset controls, system volume & UI)
 * - Persistent notification with cover art and playback actions
 * - Partial WakeLock to prevent CPU sleep during background playback
 */
class MediaPlaybackService : Service() {

    companion object {
        const val CHANNEL_ID = "tmusic_media_channel"
        const val NOTIFICATION_ID = 1001

        const val ACTION_SET_PLAYBACK_STATE = "app.tmusic.mobile.ACTION_SET_PLAYBACK_STATE"
        const val ACTION_UPDATE_METADATA = "app.tmusic.mobile.ACTION_UPDATE_METADATA"
        const val ACTION_UPDATE_POSITION = "app.tmusic.mobile.ACTION_UPDATE_POSITION"

        const val ACTION_CMD_PLAY = "app.tmusic.mobile.CMD_PLAY"
        const val ACTION_CMD_PAUSE = "app.tmusic.mobile.CMD_PAUSE"
        const val ACTION_CMD_TOGGLE = "app.tmusic.mobile.CMD_TOGGLE"
        const val ACTION_CMD_NEXT = "app.tmusic.mobile.CMD_NEXT"
        const val ACTION_CMD_PREV = "app.tmusic.mobile.CMD_PREV"

        const val EXTRA_PLAYBACK_STATE = "playback_state"
        const val EXTRA_TITLE = "title"
        const val EXTRA_ARTIST = "artist"
        const val EXTRA_ALBUM = "album"
        const val EXTRA_COVER_URL = "cover_url"
        const val EXTRA_DURATION = "duration"
        const val EXTRA_POSITION = "position"

        var isServiceRunning = false
            private set
    }

    private var mediaSession: MediaSessionCompat? = null
    private var wakeLock: PowerManager.WakeLock? = null

    private var currentTitle: String = "T-Music"
    private var currentArtist: String = ""
    private var currentAlbum: String = ""
    private var currentCoverUrl: String = ""
    private var currentDuration: Long = 0
    private var currentPosition: Long = 0
    private var isPlaying: Boolean = false
    private var currentCoverBitmap: Bitmap? = null
    private var lastLoadedCoverUrl: String = ""

    override fun onCreate() {
        super.onCreate()
        isServiceRunning = true

        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TMusic:PlaybackWakeLock")

        createNotificationChannel()
        initMediaSession()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "T-Music Воспроизведение",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Управление воспроизведением музыки в фоне и на экране блокировки"
            setShowBadge(false)
            setSound(null, null)
        }
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(channel)
    }

    private fun initMediaSession() {
        mediaSession = MediaSessionCompat(this, "TMusicSession").apply {
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() {
                    MainActivity.dispatchNativeCommand("play")
                }

                override fun onPause() {
                    MainActivity.dispatchNativeCommand("pause")
                }

                override fun onSkipToNext() {
                    MainActivity.dispatchNativeCommand("next")
                }

                override fun onSkipToPrevious() {
                    MainActivity.dispatchNativeCommand("previous")
                }

                override fun onSeekTo(pos: Long) {
                    MainActivity.dispatchNativeCommand("seekto", pos / 1000.0)
                }
            })
            isActive = true
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) return START_NOT_STICKY

        when (intent.action) {
            ACTION_SET_PLAYBACK_STATE -> {
                val state = intent.getStringExtra(EXTRA_PLAYBACK_STATE) ?: "none"
                handlePlaybackState(state)
            }
            ACTION_UPDATE_METADATA -> {
                currentTitle = intent.getStringExtra(EXTRA_TITLE) ?: "T-Music"
                currentArtist = intent.getStringExtra(EXTRA_ARTIST) ?: ""
                currentAlbum = intent.getStringExtra(EXTRA_ALBUM) ?: ""
                currentDuration = intent.getLongExtra(EXTRA_DURATION, 0L)
                val newCoverUrl = intent.getStringExtra(EXTRA_COVER_URL) ?: ""
                if (newCoverUrl != currentCoverUrl) {
                    currentCoverUrl = newCoverUrl
                    currentCoverBitmap = null
                    loadCoverBitmap(newCoverUrl)
                }
                updateMediaSessionMetadata()
                updateNotification()
            }
            ACTION_UPDATE_POSITION -> {
                currentDuration = intent.getLongExtra(EXTRA_DURATION, currentDuration)
                currentPosition = intent.getLongExtra(EXTRA_POSITION, currentPosition)
                updateMediaSessionPlaybackState()
            }
            ACTION_CMD_PLAY -> MainActivity.dispatchNativeCommand("play")
            ACTION_CMD_PAUSE -> MainActivity.dispatchNativeCommand("pause")
            ACTION_CMD_TOGGLE -> MainActivity.dispatchNativeCommand("playpause")
            ACTION_CMD_NEXT -> MainActivity.dispatchNativeCommand("next")
            ACTION_CMD_PREV -> MainActivity.dispatchNativeCommand("previous")
        }

        return START_NOT_STICKY
    }

    private fun handlePlaybackState(state: String) {
        when (state) {
            "playing" -> {
                isPlaying = true
                acquireWakeLock()
                updateMediaSessionPlaybackState()
                val notification = buildNotification()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    startForeground(
                        NOTIFICATION_ID,
                        notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
                    )
                } else {
                    startForeground(NOTIFICATION_ID, notification)
                }
            }
            "paused" -> {
                isPlaying = false
                releaseWakeLock()
                updateMediaSessionPlaybackState()
                updateNotification()
                stopForeground(STOP_FOREGROUND_DETACH)
            }
            "none", "stopped" -> {
                isPlaying = false
                releaseWakeLock()
                updateMediaSessionPlaybackState()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private fun acquireWakeLock() {
        try {
            if (wakeLock?.isHeld == false) {
                wakeLock?.acquire(2 * 60 * 60 * 1000L) // 2 hours safety limit
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun updateMediaSessionPlaybackState() {
        val state = if (isPlaying) PlaybackStateCompat.STATE_PLAYING else PlaybackStateCompat.STATE_PAUSED
        val actions = PlaybackStateCompat.ACTION_PLAY or
                PlaybackStateCompat.ACTION_PAUSE or
                PlaybackStateCompat.ACTION_PLAY_PAUSE or
                PlaybackStateCompat.ACTION_SKIP_TO_NEXT or
                PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS or
                PlaybackStateCompat.ACTION_SEEK_TO

        val playbackState = PlaybackStateCompat.Builder()
            .setActions(actions)
            .setState(state, currentPosition, 1.0f)
            .build()

        mediaSession?.setPlaybackState(playbackState)
    }

    private fun updateMediaSessionMetadata() {
        val builder = MediaMetadataCompat.Builder()
            .putString(MediaMetadataCompat.METADATA_KEY_TITLE, currentTitle)
            .putString(MediaMetadataCompat.METADATA_KEY_ARTIST, currentArtist)
            .putString(MediaMetadataCompat.METADATA_KEY_ALBUM, currentAlbum)
            .putLong(MediaMetadataCompat.METADATA_KEY_DURATION, currentDuration)

        currentCoverBitmap?.let {
            builder.putBitmap(MediaMetadataCompat.METADATA_KEY_ALBUM_ART, it)
        }

        mediaSession?.setMetadata(builder.build())
    }

    private fun loadCoverBitmap(urlStr: String) {
        if (urlStr.isBlank() || (urlStr == lastLoadedCoverUrl && currentCoverBitmap != null)) return
        // Query WebView cookies on the service thread, not after following redirects.
        val cookie = try {
            CookieManager.getInstance().getCookie(urlStr)
        } catch (_: Exception) {
            // An unavailable WebView cookie store must not interrupt playback controls.
            return
        }
        thread {
            var connection: HttpURLConnection? = null
            try {
                val url = URL(urlStr)
                if (url.protocol != "http" && url.protocol != "https") return@thread
                connection = url.openConnection() as HttpURLConnection
                connection.connectTimeout = 5000
                connection.readTimeout = 5000
                connection.doInput = true
                // Do not forward the session cookie to a redirect destination.
                connection.instanceFollowRedirects = false
                if (!cookie.isNullOrBlank()) connection.setRequestProperty("Cookie", cookie)
                if (connection.responseCode != HttpURLConnection.HTTP_OK) return@thread
                val bitmap = connection.inputStream.use { BitmapFactory.decodeStream(it) } ?: return@thread
                Handler(Looper.getMainLooper()).post {
                    if (currentCoverUrl == urlStr) {
                        currentCoverBitmap = bitmap
                        lastLoadedCoverUrl = urlStr // Cache only successful current-track loads.
                        updateMediaSessionMetadata()
                        updateNotification()
                    }
                }
            } catch (_: Exception) {
                // Metadata/controls remain usable; do not cache a failed URL.
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun updateNotification() {
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(NOTIFICATION_ID, buildNotification())
    }

    private fun buildNotification(): Notification {
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val prevIntent = PendingIntent.getService(
            this, 1,
            Intent(this, MediaPlaybackService::class.java).apply { action = ACTION_CMD_PREV },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val toggleIntent = PendingIntent.getService(
            this, 2,
            Intent(this, MediaPlaybackService::class.java).apply { action = ACTION_CMD_TOGGLE },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val nextIntent = PendingIntent.getService(
            this, 3,
            Intent(this, MediaPlaybackService::class.java).apply { action = ACTION_CMD_NEXT },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val playPauseIcon = if (isPlaying) {
            android.R.drawable.ic_media_pause
        } else {
            android.R.drawable.ic_media_play
        }
        val playPauseTitle = if (isPlaying) "Пауза" else "Играть"

        val style = androidx.media.app.NotificationCompat.MediaStyle()
            .setShowActionsInCompactView(0, 1, 2)

        mediaSession?.sessionToken?.let {
            style.setMediaSession(it)
        }

        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(currentTitle)
            .setContentText(currentArtist.ifEmpty { currentAlbum })
            .setSubText(if (currentArtist.isNotEmpty()) currentAlbum else null)
            .setContentIntent(contentIntent)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(isPlaying)
            .setStyle(style)
            .addAction(android.R.drawable.ic_media_previous, "Предыдущий", prevIntent)
            .addAction(playPauseIcon, playPauseTitle, toggleIntent)
            .addAction(android.R.drawable.ic_media_next, "Следующий", nextIntent)

        currentCoverBitmap?.let {
            builder.setLargeIcon(it)
        }

        return builder.build()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        // Clean shutdown on swipe-to-kill from Recents
        releaseWakeLock()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        isServiceRunning = false
        releaseWakeLock()
        mediaSession?.release()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
