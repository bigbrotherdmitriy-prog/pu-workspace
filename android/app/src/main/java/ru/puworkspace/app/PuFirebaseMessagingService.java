package ru.puworkspace.app;

import android.Manifest;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

/**
 * Receives pushes while the app is in the foreground and token rotations.
 * In the background FCM shows the notification itself and the tap reaches
 * {@link PuLauncherActivity} with the {@code deep_link} data key as an extra.
 */
public class PuFirebaseMessagingService extends FirebaseMessagingService {
    @Override
    public void onNewToken(@NonNull String token) {
        PushTokenStore.save(this, token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Uri link = PushLinks.trusted(message.getData().get(PushLinks.EXTRA_LINK));
        if (link == null) {
            return;
        }
        RemoteMessage.Notification notification = message.getNotification();
        String title = notification != null && notification.getTitle() != null
                ? notification.getTitle()
                : getString(R.string.push_default_title);

        PushChannels.ensure(this);
        Intent open = new Intent(Intent.ACTION_VIEW, link, this, PuLauncherActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent tap = PendingIntent.getActivity(
                this, link.hashCode(), open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, PushChannels.NOTIFICATIONS)
                .setSmallIcon(R.drawable.ic_stat_pu)
                .setContentTitle(title)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(tap);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return; // The user denied notifications.
        }
        NotificationManagerCompat.from(this).notify((int) System.currentTimeMillis(), builder.build());
    }
}
