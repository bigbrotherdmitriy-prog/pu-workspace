from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"


def test_android_wrapper_opens_only_the_production_https_origin():
    manifest = (ANDROID / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")

    assert 'android:value="https://pu-workspace.duckdns.org/new/"' in manifest
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
