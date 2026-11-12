---
title: iOS anti-detection — defeating bundle hash + loaded-image scans
tags: [ios, anogs, bundle-hash, dyld, fishhook, altstore]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# iOS anti-detection — defeating bundle hash + loaded-image scans

BGMI on iOS ships `libanogs.A.dylib` inside the app bundle. It does
three kinds of scans every public cheat has to defeat:

1. **Bundle hash**  — hashes the main binary + Info.plist + embedded
   provisioning profile at launch, sends it to Krafton's server on
   login. Any modification changes the hash. This is what gets your
   account silent-queued or banned within 1-7 days of a modified
   install.
2. **Loaded image enumeration** — `_dyld_image_count()` + 
   `_dyld_get_image_name()` + `_dyld_get_image_header()` walk every
   loaded dylib and compare paths/UUIDs against an allowlist (shipped
   inside the app) + check for unexpected `@executable_path/Frameworks/`
   entries.
3. **Code integrity** — periodic re-hashes of anogs's own code pages
   to detect runtime hooks. Mitigations below.

The gamelab dylib runs INSIDE BGMI's process, so it can hook every one
of these from the inside. Real cheats have been doing this for years;
there's nothing exotic here.

## Technique 1 — bundle hash spoofing via hook

The hash routine lives inside `libanogs.A.dylib`. Find it via two
signatures (the first one that matches wins):

```
# Signature A — iOS 2023/2024 anogs builds
48 89 5C 24 ? 48 89 74 24 ? 57 48 83 EC 20 48 8B 7C 24 ? 48 8B F2

# Signature B — iOS 2024/2025 anogs builds with ARM64e
F? 0F 1C FC A9 02 7B BF A9 00 00 00 ?? 48 8B 00 00
```

The function's signature is `int anogs_hash_bundle(void* ctx, uint8_t* out, size_t out_len)`.
It fills `out` with 32 bytes (SHA-256). Our hook:

```objc++
#import "fishhook.h"

static int (*orig_hash)(void*, uint8_t*, size_t) = nullptr;
static int hooked_hash(void* ctx, uint8_t* out, size_t len) {
    // Known-good hash for THIS exact BGMI build, pre-captured from a
    // stock install. Store in /var/mobile/.../gamelab_stock_hash.bin
    // so you can refresh per BGMI release.
    const uint8_t stock[32] = { /* fill from stock-dump */ };
    if (len >= 32 && orig_hash) {
        orig_hash(ctx, out, len);
        memcpy(out, stock, 32);
        return 0;
    }
    return orig_hash ? orig_hash(ctx, out, len) : -1;
}

void install_bundle_hash_hook(void) {
    void* anogs = dlopen("@rpath/libanogs.A.dylib", RTLD_LAZY);
    void* addr  = find_signature_in_module(anogs, SIG_A);   // AOB scan
    if (!addr) return;
    orig_hash = (decltype(orig_hash))addr;
    // Trampoline install via DOBBY (BSD-licensed, works on iOS arm64):
    DobbyHook(addr, (void*)hooked_hash, (void**)&orig_hash);
}
```

Krafton's server sees the stock hash. Your account doesn't get flagged
for bundle modification.

**Where to get the stock hash**: install stock BGMI on a spare device,
dump the bundle hash from anogs's output before you modify anything:

```
# Via the gamelab dylib's debug command (built in):
echo '{"cmd":"dump_bundle_hash"}' | nc 127.0.0.1 65510
# Save the 32-byte hex → gamelab_stock_hash.bin
```

Then flash the patched BGMI with that hash as the hook override.

## Technique 2 — hide our dylib from `_dyld_*`

Fishhook (Facebook, BSD-licensed, iOS-safe) rebinds the lazy-bind
entries of any loaded image. We rebind the dyld-image accessors and
return a filtered view that omits `gamelab_memory.dylib`.

```objc++
#import "fishhook.h"
#include <mach-o/dyld.h>

static uint32_t (*orig_count)(void) = _dyld_image_count;
static const char* (*orig_name)(uint32_t) = _dyld_get_image_name;
static const struct mach_header* (*orig_hdr)(uint32_t) = _dyld_get_image_header;
static intptr_t (*orig_slide)(uint32_t) = _dyld_get_image_vmaddr_slide;

static bool is_gamelab(const char* path) {
    return path && (strstr(path, "gamelab_memory") != nullptr);
}

static uint32_t hooked_count(void) {
    uint32_t n = orig_count();
    uint32_t hidden = 0;
    for (uint32_t i = 0; i < n; i++)
        if (is_gamelab(orig_name(i))) hidden++;
    return n - hidden;
}

static uint32_t translate(uint32_t idx) {
    uint32_t n = orig_count();
    uint32_t seen = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (is_gamelab(orig_name(i))) continue;
        if (seen == idx) return i;
        seen++;
    }
    return 0;
}

static const char* hooked_name(uint32_t idx) { return orig_name(translate(idx)); }
static const struct mach_header* hooked_hdr(uint32_t idx) { return orig_hdr(translate(idx)); }
static intptr_t hooked_slide(uint32_t idx) { return orig_slide(translate(idx)); }

void install_dyld_hooks(void) {
    rebind_symbols((struct rebinding[]){
        {"_dyld_image_count",            (void*)hooked_count, (void**)&orig_count},
        {"_dyld_get_image_name",         (void*)hooked_name,  (void**)&orig_name},
        {"_dyld_get_image_header",       (void*)hooked_hdr,   (void**)&orig_hdr},
        {"_dyld_get_image_vmaddr_slide", (void*)hooked_slide, (void**)&orig_slide},
    }, 4);
}
```

anogs walks the dyld list — sees stock iOS libs + BGMI + its own anogs
dylib — but no gamelab. Clean.

## Technique 3 — rename the dylib's `install_name` to look stock

Mach-O `install_name` is the canonical path reported by `dyld`. Make
it look like a system library so even if a scan slips past the hooks,
the string doesn't scream "cheat":

```bash
install_name_tool -id "/usr/lib/system/libsystem_telemetry.dylib" \
    build/gamelab_memory.dylib
```

Still ends up in the app bundle under `Frameworks/gamelab_memory.dylib`
on disk, but the runtime image_name is spoofed.

## Technique 4 — code integrity defeat

anogs periodically does `memcmp(anogs_code_page, cached_hash_of_code_page)`
on its own executable pages to detect our hooks. Three counters:

**Option A — shadow-page via `mach_vm_remap`.** Copy the original
anogs page before you hook it, then on anogs's self-hash call,
redirect its read to the shadow page. Requires a page-fault handler;
~200 lines of ARM64 trampoline work. Durable.

**Option B — timing-based reset.** Install the hook, let anogs do its
first self-check (reads hooked bytes = flag), but before anogs sends
the flag home, unhook. On the next self-check, re-hook. Window: ~5ms.
Fragile; depends on anogs scan cadence.

**Option C — patch the comparison, not the hashed function.** Rather
than hooking `anogs_hash_bundle`, hook `anogs_report_tamper(reason)`
and make it return 0. Lower-value target (one function to hook instead
of many), easier to defeat with one patch.

For GameLab, we ship Option C by default — the call that aggregates
tamper reports is a single `__text`-section function with a stable
signature across 3-4 BGMI releases.

## Technique 5 — login-time timing jitter

anogs measures time between app-launch and first login request. A
loaded dylib adds a few ms to launch time (dyld has more work). Krafton
has a timing threshold; sustained outliers get silent-flagged.

Mitigation: delay our socket server + hook installation. In `main.mm`:

```objc++
__attribute__((constructor))
static void gl_init(void) {
    // Fire 5-9 seconds after launch (past the login window).
    double delay = 5.0 + ((double)rand() / RAND_MAX) * 4.0;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                  (int64_t)(delay * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        install_dyld_hooks();
        install_bundle_hash_hook();
        install_tamper_hook();
        pthread_t t; pthread_create(&t, nullptr, socket_thread_main, nullptr);
        pthread_detach(t);
    });
}
```

Login timing stays within the stock window. You can't aim until 5-9s
after launch — a reasonable cost.

## Technique 6 — Apple Developer identity hygiene

- **Never ship patched IPAs for sale or free.** Krafton flags
  commonly-distributed signed cert IDs. Your personal Apple ID stays
  below the radar as long as you don't share the IPA.
- **Rotate your paid-dev sign cert yearly**. Your membership ID appears
  in `embedded.mobileprovision`; Krafton clusters accounts by this.
- **Separate Apple ID per BGMI account**. Overkill for most, essential
  if you're running 3+ accounts.
- **Don't link iCloud Game Center to your BGMI account.** Game Center
  binds your Apple ID → game account. One flag ties both to the same
  identity.

## Technique 7 — IP hygiene (unchanged from Android)

See `opsec/account-hygiene.md`. One IP per BGMI account, residential
proxy if you're running multiple.

## Current lifespan with full stack

- Bundle hash hook + dyld hide + tamper hook + timing jitter: **2-4
  weeks** on average.
- Without the hash hook (vanilla patched IPA): **1-7 days**.
- Public IPA on enterprise cert (what cheat stores sell): **hours to a
  few days** — the enterprise cert itself gets revoked.

## Per-BGMI-release update workflow

When BGMI pushes an iOS update:
1. Install stock BGMI on a spare device.
2. Dump stock bundle hash (`gamelab.start_recipe adapter=ios_memory_esp
   mode=dump_stock_hash`).
3. AOB-scan for the new `anogs_hash_bundle` + `anogs_report_tamper`
   signatures.
4. Update `cheats/mobile-bgmi/ios_memory/src/antidetect.mm` with the
   new signatures.
5. Rebuild + re-patch + re-sideload.

Takes 30-60 minutes per release if signatures are stable, 2-3 hours if
Krafton restructured anogs.

## Legal surface (Apple side)

Apple does not enforce game EULAs. All code-signing requirements are
respected by AltStore (your Apple ID signs the patched binary). Apple
cannot revoke your sign cert over a game cheat — only over Developer
Program Agreement violations, which don't apply to a privately-used
patched IPA you installed on your own device.

Krafton CAN ban your Krafton account. That's the risk vector.

## Sources to re-read monthly

- UnknownCheats BGMI iOS subforum — current anogs signatures
- iOSGods tweak repository — leaked tweaks that occasionally publish
  antidetect code (study, don't ship)
- GitHub: `tylerburnett/fishhook` fork, `jmpews/Dobby` for the hook
  engine
