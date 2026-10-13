package ru.puworkspace.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;

final class PushChannels {
    /** Must match ANDROID_CHANNEL_ID in backend/app/integrations/push.py. */
    static final String NOTIFICATIONS = "pu_workspace_alerts";

    private PushChannels() {
    }

    static void ensure(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel(NOTIFICATIONS) != null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                NOTIFICATIONS,
                context.getString(R.string.push_channel_name),
                NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription(context.getString(R.string.push_channel_description));
        manager.createNotificationChannel(channel);
    }
}
