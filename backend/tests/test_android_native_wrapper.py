from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"


def test_android_wrapper_opens_only_the_production_https_origin():
    manifest = (ANDROID / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")

    assert 'android:value="https://puworkspace.ru/new/"' in manifest
    assert 'android:host="puworkspace.ru"' in manifest
    assert "duckdns" not in manifest
    assert 'android:scheme="https"' in manifest
    assert 'android:usesCleartextTraffic="false"' in manifest
    assert 'android:allowBackup="false"' in manifest
    assert 'android.permission.INTERNET' in manifest


def test_android_release_signing_uses_environment_without_committed_secrets():
    build = (ANDROID / "app/build.gradle").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for variable in (
        "PU_ANDROID_KEYSTORE",
        "PU_ANDROID_STORE_PASSWORD",
        "PU_ANDROID_KEY_ALIAS",
        "PU_ANDROID_KEY_PASSWORD",
    ):
        assert f'System.getenv("{variable}")' in build
    assert "*.jks" in gitignore
    assert "*.keystore" in gitignore
    assert "storePassword \"" not in build
    assert "keyPassword \"" not in build


def test_android_wrapper_keeps_google_oauth_in_the_browser_session():
    readme = (ANDROID / "README.md").read_text(encoding="utf-8")

    assert "Trusted Web Activity" in readme
    assert "Chrome session" in readme
    assert "without embedding credentials" in readme


JAVA = ANDROID / "app/src/main/java/ru/puworkspace/app"


def test_android_push_client_is_wired_into_the_manifest():
    manifest = (ANDROID / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    build = (ANDROID / "app/build.gradle").read_text(encoding="utf-8")

    assert 'android:name=".PuLauncherActivity"' in manifest
    assert 'android:name=".PuFirebaseMessagingService"' in manifest
    assert "com.google.firebase.MESSAGING_EVENT" in manifest
    assert "android.permission.POST_NOTIFICATIONS" in manifest
    assert 'id "com.google.gms.google-services"' in build
    assert '"com.google.firebase:firebase-messaging:24.1.0"' in build


def test_android_push_hands_token_to_spa_in_fragment_and_opens_only_own_links():
    launcher = (JAVA / "PuLauncherActivity.java").read_text(encoding="utf-8")
    links = (JAVA / "PushLinks.java").read_text(encoding="utf-8")
    channels = (JAVA / "PushChannels.java").read_text(encoding="utf-8")
    backend_push = (ROOT / "backend/app/integrations/push.py").read_text(encoding="utf-8")

    assert "extends LauncherActivity" in launcher
    assert 'encodedFragment("fcm=" + Uri.encode(token))' in launcher
    assert '"puworkspace.ru".equals(uri.getHost())' in links
    assert '"https".equals(uri.getScheme())' in links
    # The channel id is shared by the Android client and the backend payload.
    assert 'NOTIFICATIONS = "pu_workspace_alerts"' in channels
    assert 'ANDROID_CHANNEL_ID = "pu_workspace_alerts"' in backend_push
    # The deep-link data key is shared too.
    assert 'EXTRA_LINK = "deep_link"' in links
    assert '"data": {"deep_link": self.link}' in backend_push


def test_firebase_config_is_never_committed():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    readme = (ANDROID / "README.md").read_text(encoding="utf-8")

    assert "google-services.json" in gitignore
    assert not list(ROOT.joinpath("android").rglob("google-services.json"))
    assert "android/app/google-services.json" in readme
