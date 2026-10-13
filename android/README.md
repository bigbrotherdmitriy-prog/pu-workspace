# PU Workspace for Android

This module builds a signed Trusted Web Activity for the production PU Workspace PWA.
It uses the user's Chrome session, so Google OAuth and secure file/camera selection continue
to work without embedding credentials in the APK.

Signing values are supplied only through `PU_ANDROID_*` environment variables. Never commit
the release keystore or its passwords.

## Push notifications (FCM)

`PuFirebaseMessagingService` shows pushes in the `pu_workspace_alerts` channel; tapping one
opens its deep link (`https://puworkspace.ru/new/...`) in the Trusted Web Activity.
`PuLauncherActivity` passes the device's FCM token to the web app in the URL fragment
(`https://puworkspace.ru/new/#fcm=<token>`); the web app registers it with
`POST /api/notifications/devices/register`, removes the fragment and revokes the token
on sign-out. The first launch after install only fetches the token, so it is handed
over on the next start.

The Firebase project config is not stored in git. Before building:

1. Firebase console → Project settings → Android app `ru.puworkspace.app` →
   download `google-services.json`.
2. Put it at `android/app/google-services.json` (ignored by `.gitignore`).
3. Build as usual, e.g. `gradle :app:assembleRelease` with the `PU_ANDROID_*` signing
   variables. Without the file the Google Services plugin stops the build.

The backend sends pushes only when `FCM_SERVICE_ACCOUNT_JSON_PATH` points to the
service account JSON of the same Firebase project; `/api/readiness` (`fcm_push`)
shows why the channel is off otherwise.

## App Links

The backend serves `https://puworkspace.ru/.well-known/assetlinks.json` from the
`PU_ANDROID_CERT_SHA256` environment variable (SHA-256 of the release signing
certificate, e.g. from `keytool -list -v -keystore <release.jks>`). Until it is set
the endpoint answers 503 and Chrome shows the URL bar in the app.
