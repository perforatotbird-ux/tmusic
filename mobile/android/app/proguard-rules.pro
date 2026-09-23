# Add project specific ProGuard rules here.
-keep public class androidx.webkit.** { *; }
-keep public class androidx.media.** { *; }
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keep class app.tmusic.mobile.** { *; }
