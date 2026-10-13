package ru.puworkspace.app;

import android.content.Context;
import android.content.SharedPreferences;

/** Last FCM registration token; handed to the SPA on the next launch. */
final class PushTokenStore {
    private static final String PREFS = "pu_push";
    private static final String KEY_TOKEN = "fcm_token";

    private PushTokenStore() {
    }

    static void save(Context context, String token) {
        if (token == null || token.isEmpty()) {
            return;
        }
        prefs(context).edit().putString(KEY_TOKEN, token).apply();
    }

    static String load(Context context) {
        return prefs(context).getString(KEY_TOKEN, null);
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
