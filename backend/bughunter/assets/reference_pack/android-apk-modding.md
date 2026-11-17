# Android APK inspection and offline modding

Authored synthesis; reviewed 2026-11-16. Practical workflow for decoding, editing, rebuilding and installing an Android package.

## Why offline games are the practical target

A value the server owns (account balance, cloud save, live leaderboard) cannot be changed by editing the client — the edit either does nothing or gets reverted. Values that live only on the device (offline currency counter, local save file, local config) are the ones that respond. Quick test: if the game still runs in airplane mode, its state is probably local.

## The workflow

**1. Obtain the package.** Pull it from the device you installed it on, which keeps provenance clean:

```
adb shell pm list packages | grep -i <name>
adb shell pm path <package.name>
adb pull /data/app/.../base.apk
```

Split APKs (`base.apk` plus `split_config.*`) must be kept together and reinstalled together with `adb install-multiple`. An APK from a mirror site is an unverified binary: record its SHA-256 and treat it as untrusted input, which is what this app's file inspection does by default.

**2. Record identity before touching it.**

```
sha256sum base.apk
aapt dump badging base.apk | head        # package, versionCode, versionName
apksigner verify --print-certs base.apk  # original signer
```

**3. Read before you write.** Decompile to Java-like source for understanding:

```
jadx -d out/ base.apk          # readable Java, best for finding logic
apktool d base.apk -o work/    # smali + resources, this is what you rebuild from
```

[jadx](https://github.com/skylot/jadx) is for reading. [apktool](https://apktool.org/) is for round-tripping. Use both: find it in jadx, change it in apktool's smali.

**4. Find the value.** Search the decompiled source for the concept, not a guess:

```
grep -rn "coin\|currency\|getCoins\|addCoins" out/sources/ | head -40
grep -rn "SharedPreferences\|getInt(" out/sources/ | grep -i coin
```

Three common storage patterns, each with a different fix:
- **SharedPreferences XML** — `/data/data/<pkg>/shared_prefs/*.xml`. Often the easiest target and needs no rebuild at all on a rooted device or emulator: edit the value in place.
- **A local database or save file** — SQLite or a serialized blob in `/data/data/<pkg>/files/`. Parse, edit, fix any checksum, write back to a copy.
- **A field in code** — needs the smali edit below.

**5. Edit smali.** In `work/smali*/…/SomeClass.smali`, a getter returning a field:

```smali
.method public getCoins()I
    .locals 1
    iget v0, p0, Lcom/example/Player;->coins:I
    return v0
.end method
```

becomes a constant:

```smali
.method public getCoins()I
    .locals 1
    const v0, 0x3b9ac9ff          # 999999999
    return v0
.end method
```

Keep `.locals` at least as large as the registers you use. `const` for 32-bit ints, `const-wide` for longs/doubles. A spend routine is usually better neutralised by making the "can afford" check always true than by writing a huge balance, which can overflow into a negative number.

**6. Rebuild, align, sign.** An APK must be signed to install; your own debug key is fine for local use.

```
apktool b work/ -o modded.apk
zipalign -p -f 4 modded.apk aligned.apk
apksigner sign --ks ~/.android/debug.keystore --ks-pass pass:android --out final.apk aligned.apk
apksigner verify --print-certs final.apk
```

**7. Install and verify.** The signature changed, so the original must be uninstalled first (this deletes its local data):

```
adb uninstall <package.name>
adb install final.apk
```

Then confirm the change in the running app. A rebuild that installs is not a rebuild that works.

## What breaks, and the honest limits

- **Signature and integrity checks.** Many games verify their own signature or a file hash at startup and refuse to run when it changes. Defeating that is a deeper reverse-engineering job and is exactly what anti-tamper is designed to resist.
- **Native code.** Logic in `lib/*/lib*.so` is not in smali at all. That needs a disassembler (this app's REA tooling, or Ghidra) and patching the native binary.
- **Obfuscation.** R8/ProGuard renames classes to `a.b.c`. Search for string constants, resource IDs and framework calls (`getSharedPreferences`, `getInt`) instead of names.
- **Server validation.** Anything the server re-checks will be reverted or flagged.
- **Play Integrity / SafetyNet.** A modified build fails attestation; features gated behind it stop working.

## Doing this from this app

Ask in Chat for the whole chain in one message: download the APK, import it as a Library resource (records filename, size and SHA-256 without executing it), install `jadx` and `apktool` through the supported installer, decompile in the task workspace, find the value, edit the smali, rebuild, sign and install, reporting each step's real output. Rebuilding and installing need the files and commands grants, so run the task in Full access.

Reference for the Android package format: [Android application fundamentals](https://developer.android.com/guide/components/fundamentals), and for signing: [apksigner documentation](https://developer.android.com/tools/apksigner).
