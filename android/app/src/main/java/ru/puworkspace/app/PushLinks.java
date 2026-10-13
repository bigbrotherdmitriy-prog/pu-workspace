package ru.puworkspace.app;

import android.net.Uri;

/** Only links into the PU Workspace SPA may be opened from a push. */
final class PushLinks {
    static final String APP_ORIGIN = "https://puworkspace.ru";
    static final String APP_PATH_PREFIX = "/new/";
    static final String EXTRA_LINK = "deep_link";

    private PushLinks() {
    }

    static Uri trusted(String link) {
        if (link == null || link.isEmpty()) {
            return null;
        }
        Uri uri = Uri.parse(link);
        if (!"https".equals(uri.getScheme())
                || !"puworkspace.ru".equals(uri.getHost())
                || uri.getPort() != -1
                || uri.getPath() == null
                || !uri.getPath().startsWith(APP_PATH_PREFIX)) {
            return null;
        }
        return uri;
    }
}
