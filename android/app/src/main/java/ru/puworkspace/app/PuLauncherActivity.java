package ru.puworkspace.app;

import android.Manifest;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;

import com.google.androidbrowserhelper.trusted.LauncherActivity;
import com.google.firebase.messaging.FirebaseMessaging;

/**
 * Trusted Web Activity launcher that
 * - opens the deep link of a tapped push instead of the start page, and
 * - passes the FCM token to the SPA in the URL fragment ({@code #fcm=<token>}).
 *
 * The fragment never reaches the server; the SPA registers the token through
 * POST /api/notifications/devices/register and strips the fragment.
 */
public class PuLauncherActivity extends LauncherActivity {
    private static final int NOTIFICATION_PERMISSION_REQUEST = 4201;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        PushChannels.ensure(this);
        refreshToken();
        super.onCreate(savedInstanceState);
        requestNotificationPermission();
    }

    @Override
    protected Uri getLaunchingUrl() {
        Uri uri = super.getLaunchingUrl();
        // A push tapped while the app was in the background arrives as extras
        // (FCM copies the data keys, so "deep_link" is one of them).
        Uri pushLink = PushLinks.trusted(getIntent().getStringExtra(PushLinks.EXTRA_LINK));
        if (pushLink != null) {
            uri = pushLink;
        }
        String token = PushTokenStore.load(this);
        if (token == null || PushLinks.trusted(uri.toString()) == null) {
            return uri;
        }
        return uri.buildUpon().encodedFragment("fcm=" + Uri.encode(token)).build();
    }

    private void refreshToken() {
        try {
            FirebaseMessaging.getInstance().getToken()
                    .addOnSuccessListener(token -> PushTokenStore.save(this, token));
        } catch (IllegalStateException ignored) {
            // Firebase is not initialised (APK built without google-services.json):
            // the app still works, only push stays off.
        }
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return;
        }
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST);
        }
    }
}
