# PU Workspace for Android

This module builds a signed Trusted Web Activity for the production PU Workspace PWA.
It uses the user's Chrome session, so Google OAuth and secure file/camera selection continue
to work without embedding credentials in the APK.

Signing values are supplied only through `PU_ANDROID_*` environment variables. Never commit
the release keystore or its passwords.
