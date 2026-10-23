# Owner-local KPS Driver signing

The owner chose to keep the permanent signing key on their own computer.
No key is created by CI, in chat, in a build artifact, or on an agent host.
The helper below creates it only when the owner runs the explicit interactive
`create` command on that computer. It never replaces an existing key.

## One-time preparation on the owner computer

Install Python 3 and JDK 17 (Android Studio includes a JDK). Make `keytool`
available on PATH or set `JAVA_HOME` to that JDK. Review the script in
`android-driver/tools/local_signing.py` before running it.

Choose two **new, dedicated folders outside every Git checkout**: the main
key folder on the owner computer, and a backup folder on an owner-controlled
encrypted external drive or protected backup location. Keep the password in
your password manager, separately from both copies. The helper protects new
folders with owner-only permissions (Windows uses `icacls`). It verifies the
encrypted backup byte-for-byte before reporting success.

Windows example (change the drive and username paths to your actual locations):

```powershell
py android-driver/tools/local_signing.py create --keystore "$env:USERPROFILE\Documents\KPS-Driver-Signing\kps-driver.p12" --backup E:\KPS-Driver-Backup\kps-driver.p12
```

The password is entered locally using a hidden prompt, twice. No password is
passed as a command-line argument or saved in the source. The alias defaults
to `kps-driver`. The public `certificate-sha256.txt` identifies this signing
certificate; keep it with the release records. Protect the private `.p12` file.

If a company release key already exists, reuse it; do not create a replacement.
The helper's build command expects the same store/key password, as used by the
PKCS12 key it creates. Existing keys with different passwords can use Android
Studio or the documented protected Gradle configuration instead.

## Build and verify on that same computer

Use the reviewed KPS Driver source checkout. Install Android SDK 35, Build
Tools 35.0.0, JDK 17, and Gradle 8.11.1. Set `ANDROID_HOME` to the SDK location.

```powershell
py android-driver/tools/local_signing.py build --keystore "$env:USERPROFILE\Documents\KPS-Driver-Signing\kps-driver.p12"
```

The helper uses the existing protected Gradle signing configuration, compiles
and lints the release, and runs `apksigner verify`. It checks that the compiled
APK certificate matches the selected owner's key before reporting success.
Output: `android-driver/app/build/outputs/apk/release/app-release.apk`.
The helper never uploads the private key or APK and does not install on phones.
No Gradle configuration cache is used for the signing build.

The owner computer can be turned off after signing/installing the application.
It is needed again only for another native build/signing operation.

## Acceptance still outstanding

A valid signature is not Google approval and does not itself resolve the
original Play Protect warning. Preserve package/signing identity, inspect the
exact warning, and follow Google's supported review route if needed. Test the
signed APK on a company Android: login, shift open/close, lock screen, WhatsApp,
GPS/network recovery, manager location freshness, and photo evidence.
Do not distribute the blocked debug APK or an unsigned release to drivers.
