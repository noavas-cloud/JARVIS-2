#!/usr/bin/env python3
"""Build the installable APK with a persistent, private signing identity.

Set JAVA_HOME and ANDROID_HOME first. No service credentials enter the APK.
The signing key is deliberately outside the source and download directories.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SIGNING = Path.home() / "Library/Application Support/JARVIS/AndroidSigning"
SIGNING.mkdir(parents=True, exist_ok=True)
SIGNING.chmod(0o700)
record = SIGNING / "signing.json"
keystore = SIGNING / "jarvis-release.p12"
env = os.environ.copy()
if record.exists():
    data = json.loads(record.read_text())
    if not keystore.is_file():
        raise SystemExit("Signing record exists but keystore is missing. Restore it before building updates.")
else:
    if keystore.exists():
        raise SystemExit("Keystore exists but signing record is missing. Restore the record; do not replace the key.")
    data = {"alias": "jarvis-android", "password": secrets.token_urlsafe(36)}
    java_home = env.get("JAVA_HOME", "")
    keytool = str(Path(java_home) / "bin/keytool") if java_home else "keytool"
    env["JARVIS_STORE_PASSWORD"] = data["password"]
    subprocess.run([keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
                    "-alias", data["alias"], "-storepass:env", "JARVIS_STORE_PASSWORD",
                    "-keypass:env", "JARVIS_STORE_PASSWORD", "-keyalg", "RSA", "-keysize", "3072",
                    "-validity", "10000", "-dname", "CN=JARVIS Android, OU=Personal, O=JARVIS, C=TR"], env=env, check=True)
    fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output)
    keystore.chmod(0o600)
env.update(JARVIS_KEYSTORE=str(keystore), JARVIS_STORE_PASSWORD=data["password"],
           JARVIS_KEY_ALIAS=data["alias"], JARVIS_KEY_PASSWORD=data["password"])
gradle = shutil.which("gradle") or str(ROOT / "gradlew")
subprocess.run([gradle, ":app:assembleRelease", ":app:lintRelease", "--console=plain"], cwd=ROOT, env=env, check=True)
apk = ROOT / "app/build/outputs/apk/release/app-release.apk"
if not apk.is_file():
    raise SystemExit("Signed release APK not found; no distribution was written.")
sdk = Path(env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT", ""))
signer = sdk / "build-tools/35.0.0/apksigner"
subprocess.run([str(signer), "verify", "--verbose", str(apk)], env=env, check=True)
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
destination = dist / "JARVIS-Android.apk"
shutil.copy2(apk, destination)
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(dist / "SHA256SUMS.txt").write_text(digest + "  JARVIS-Android.apk\n")
print("Verified APK:", destination)
