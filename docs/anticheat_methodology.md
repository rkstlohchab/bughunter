# Anticheat Methodology — Architecture, Detection Vectors, Derivation Workflow

Reference synthesis for working against modern anticheat systems: strategy +
per-build derivation workflow + defender-side design + analysis-only RE. All
content is reconstructed from public reversing writeups, conference talks,
academic papers, and vendor documentation; no current-build target-pointed
weapon code is in this document (that case holds at the base layer regardless
of project config — see `AGENTS.md` discussion).

## Contents

1. [AC internals — Vanguard, VAC, ACE](#1-ac-internals)
2. [Detection-vector map per AC](#2-detection-vector-map)
3. [Generic kernel + mapper RE methodology on your own driver + test rig](#3-generic-kernel--mapper-re-methodology)
4. [Defender-side AC — reference design for your own game](#4-defender-side-ac-reference-design)
5. [Analysis-only RE workflow on installed clients](#5-analysis-only-re-workflow)

---

## 1. AC internals

### 1.1 Riot Vanguard (Valorant)

Vanguard is built around three load-order principles: **boot-start**,
**anti-strip**, and **continuous attestation**. It is the most aggressive
consumer AC shipped at scale, and its design makes most pre-2020 cheat
strategies obsolete.

**Load order.** `vgk.sys` registers as a `SERVICE_BOOT_START` driver in the
`Early-Launch` group order. Practically: on a cold boot, Vanguard's driver is
resident and instrumenting the kernel before `smss.exe`, before `csrss.exe`,
before `winlogon.exe`, before Explorer, before any user-mode process has a
chance to run. The load-order race that defeated lazy-loaded AC (EAC v1-era,
BattlEye v1-era) is lost for Vanguard by construction. The only way around
boot-start is to intercept the kernel's early init path itself — which is
where hypervisor and boot-sector work sits.

**Hypervisor binding.** On modern Windows builds with VBS available, Vanguard
turns on and requires HVCI (Hypervisor-enforced Code Integrity). HVCI puts
the Windows kernel inside a VTL-0 memory container; the hypervisor sits in
VTL-1 and refuses any Ring-0 memory write to kernel pages unless the writing
driver is signed by an HVCI-approved cert. This closed the historical path of
"map an unsigned driver, perform your work, unload." The practical options
reduce to:

- Live inside an EV-signed driver you own (ToB-filed, Microsoft-attested WHQL).
- Exploit a signed driver with a known vulnerability (the "BYOVD" pattern,
  which is noisy and OS-blacklist-tracked via the Microsoft vulnerable driver
  blocklist).
- Boot-configuration path (debug-boot entry, test-signing mode) — these are
  detected by Vanguard's boot-state attest.
- Hardware-side (DMA card, firmware) — bypasses HVCI entirely because the
  attack surface is not Ring-0 Windows.

**Process graph.** On boot, Vanguard registers callbacks for every process
creation via `PsSetCreateProcessNotifyRoutineEx`. It builds a parent-child
tree of all processes since boot and holds it in kernel memory. When
`VALORANT-Win64-Shipping.exe` starts, Vanguard attests the process's parent
chain against expected parents (the launcher → shim → game pattern). A
process whose creation path has a hole (parent was created at a time when
Vanguard's callback wasn't yet installed, or parent's handle to child was
created via a non-standard API) raises a flag.

**Callbacks + ETW.** Vanguard installs:

- `PsSetCreateProcessNotifyRoutineEx` — process lifecycle.
- `PsSetCreateThreadNotifyRoutine` — thread lifecycle.
- `PsSetLoadImageNotifyRoutine` — driver + DLL loads.
- `ObRegisterCallbacks` with `OB_OPERATION_HANDLE_CREATE` and
  `OB_OPERATION_HANDLE_DUPLICATE` on `PsProcessType` and `PsThreadType` — any
  handle opened to a game process from anywhere is observable and the
  callback can strip the access mask.
- ETW providers: `Microsoft-Windows-Kernel-Process`,
  `Microsoft-Windows-Kernel-Memory`, and custom Riot providers for memory
  region change events.

**Anti-strip.** The `vgk.sys` driver includes a periodic self-hash routine
that computes the live in-memory CRC/hash of a known range of its own
`.text` section and compares against the embedded expected value. A modified
driver on disk whose in-memory image was swapped, an unlinked `LDR_DATA_TABLE_ENTRY`,
a page remap, or even a kernel-stepping hook pattern breaks the hash. The
period is randomized but short.

**Module walks.** Vanguard periodically walks:

- `PsLoadedModuleList` — looking for unsigned pages, unexpected `DllName`s,
  entries whose `DllBase` doesn't match a mapped image.
- `KeServiceDescriptorTable` — SSDT entries that don't match the kernel's
  expected ntos pointers (SSDT hook pattern).
- The IDT — entries whose handlers point outside kernel ranges.
- Kernel pools — walking `NonPagedPool` allocations for strings and
  signatures.
- User-mode PEB `LDR_DATA_TABLE_ENTRY` lists in the game process — any DLL
  in the game's address space whose path isn't a known-good module raises
  a signal.

**Deferred ban.** Vanguard rarely bans in-session. Signals are correlated
server-side with other players' telemetry across matches and a ban is issued
in a batch days-to-weeks later. This defeats "test the cheat, iterate on
same account" development loops: you don't learn which cheat variant was
detected until long after shipping.

**Cat-and-mouse history.**

- **2020 Q2** (launch): user-space cheats worked for weeks. Vanguard
  tightened user-mode protections: `PAGE_GUARD` on game memory regions,
  hooks on `NtReadVirtualMemory` to game process from outside.
- **2020 Q3-Q4**: kernel-cheat work moved to vulnerable signed drivers
  (BYOVD). Vanguard responded with a driver-blocklist telemetry pass and
  started flagging matching driver loads.
- **2021**: DMA / hardware cheats (Fuser, Rysen) rose. Vanguard added
  heuristic checks on IOMMU state, USB enumeration (DMA adapters show as
  specific USB VID/PID signatures), motherboard-specific PCIe link training
  signatures.
- **2022-23**: external aim-assist via screen OCR + hardware mouse (Arduino
  Leonardo HID emulator, KMBox) rose as the dominant detected-free category.
  Vanguard added anti-screenshot layers on the game window (DWM composition
  bypass detection) and PID cross-reference checks with system HID devices.
- **2024**: HWID-ban escalation on repeat offenders. Hardware fingerprint
  combining TPM key, motherboard serial, CPU serial, disk volume IDs.

### 1.2 Valve VAC + VACnet (CS:GO / CS2)

VAC is older (2002 origin) and hybrid — a user-mode scanner paired with
server-side behavioral analysis. It is significantly less aggressive than
Vanguard, which is why third-party community anti-cheats (FACEIT, ESEA)
historically filled the gap for competitive play.

**User-mode signature scan.** VAC's client component (`csgo.exe` on CS:GO,
`cs2.exe` on CS2) performs rolling signature scans against the process
address space. Scans use pattern matching against a database Valve ships
updates to. Lazy: runs on game boot, then on intervals (~15-30 minutes).
Covers:

- Module path and name enumeration: `EnumProcessModules`,
  `GetModuleFileNameEx`. Any DLL in the game's address space whose
  `ImagePath` doesn't match a Valve-known module gets logged.
- Walking the PEB `LDR_DATA_TABLE_ENTRY` list directly (defeats trivial
  `PsSetLoadImageNotifyRoutine` filtering).
- Byte patterns on memory regions — specific sequences known to appear in
  common cheat binaries.
- Driver enumeration: `EnumDeviceDrivers`, `GetDeviceDriverFileName` —
  checking for recently-loaded drivers against a reference set.

**VACnet.** Server-side ML on in-match behavior. Trained on labeled
Overwatch data (human reviewers tagging demos). Features include:

- Mouse delta distribution (unnaturally precise micro-corrections).
- Crosshair placement entropy (bot-like fixed-angle holds).
- Pre-fire timing (shooting through walls before hearing/seeing the enemy).
- Wallbang correlation (hitting through walls at impossible frequencies).
- Snap-to-head signatures (acceleration curve on target-acquisition
  movements).

Bans issued in waves, delayed by days to weeks, with low false-positive
tolerance.

**Overwatch.** Crowdsourced demo review by trusted community members.
Separate ban path from VAC itself. Players flag suspect demos; qualified
reviewers watch and vote; votes contribute to a ban decision.

**Weak points historically.**

- Signature-only client scan misses polymorphic cheats (packed, obfuscated,
  or with randomized string encryption).
- VAC rarely touches kernel-mode. A trivial driver that just reads process
  memory (no hooks, no callbacks, no PEB modification) has historically
  evaded it.
- VACnet is sensitive to extreme patterns; legit-style cheats (low FOV
  aim, delayed activation, randomized mouse jitter) evade it.
- Overwatch defeats visual detection but not fundamentally new vectors.
- CS2's VAC is largely a port of CS:GO's VAC with some polish; core
  mechanics unchanged.

### 1.3 Tencent ACE (BGMI, PUBG Mobile, other Tencent titles)

ACE is mobile-focused. Primarily targets Android; iOS shares some
components but is less deeply integrated due to Apple's sandboxing.

**Native libraries.** `libanogs.so` and `libapmons.so` are the two main
native components, loaded into the game process at startup. They:

- Hook JNI boundaries to inspect every Java-side API call from the game's
  Dalvik/ART runtime.
- Signature-check Dalvik ClassLoader paths — if a non-standard ClassLoader
  is injected (e.g. Xposed), detection fires.
- Intercept `ptrace`, `/proc/[pid]/mem` reads, and the Android inter-process
  memory APIs — anyone reading game memory from outside the process raises
  a flag.
- Install SIGCHLD and anti-debug protection on the game process.

**System service walks.** ACE enumerates:

- `PackageManager.getInstalledApplications()` — looks for known modded tool
  package names (GameGuardian signatures, Lucky Patcher, Virtual Xposed,
  SandBox etc.). Checks package signatures, not just names.
- `ActivityManager.getRunningServices()` — active services whose name or
  signature matches a known-bad set.
- `getSystemProperty("ro.debuggable")`, `ro.secure`, `ro.build.tags` —
  checking for userdebug / rooted builds.

**Root / kernel detection.** On a rooted device, ACE checks:

- `/proc/self/maps` — for memory regions mapped by known malicious
  libraries.
- `/proc/net/*` — looking for specific listening sockets that indicate
  debugging/remote control infrastructure.
- `/proc/version` — custom kernel signatures.
- `/proc/mounts` — for overlay / tmpfs mounts indicating Magisk / Zygisk
  module presence.
- `su` binary presence at standard paths (`/system/bin/su`,
  `/system/xbin/su`, `/sbin/su`).

**HWID chains.** Multiple device fingerprints compose a stable identity:

- IMEI (where available; Android 10+ requires special permission).
- Android ID (per-app on 8+, but cross-app via GSF Android ID).
- Google Services Framework (GSF) ID.
- Build fingerprint composite: `ro.product.model`,
  `ro.build.fingerprint`, `ro.hardware`.
- Sensor noise pattern (accelerometer / gyroscope idle-state fingerprint).
- MAC address (`/sys/class/net/wlan0/address` where readable).
- Secure Element / Keystore attestation key (on devices with StrongBox).

Bans at the HWID level survive reinstall and factory reset on most
devices. Full device-level unban requires replacing the SIM, changing
factory-stored fingerprints, or physical device change.

**Network-layer anti-tamper.**

- Per-session HMAC on packet contents with a server-provided session key.
- Proprietary TLS wrapping over the game protocol — not standard TLS, so
  MITM proxies don't parse the stream.
- Sequence counters per channel with replay protection.
- Server-side ping / latency fingerprinting (detects tunnel/VPN overhead
  patterns).

**Signature attestation.** ACE checksums `libanogs.so` at runtime and
compares against a server-provided expected value. Hooking Frida,
substituting a patched lib, or `ptrace`-attaching the game process all
break the self-attest.

**Dynamic analysis difficulty.** ACE is heavily obfuscated with VM
protection (Themida-like on x86 bits) and OLLVM-style on ARM. String
encryption is pervasive. Reverse engineering is doable but each iteration
takes days, and new game versions patch the RE'd symbols on roughly a
weekly cadence.

---

## 2. Detection-vector map

The map is what makes per-build derivation possible — if you know every
path each AC walks, you know which surface area you need to leave clean.

### 2.1 Vanguard

| Vector | Scope | Frequency | Signal strength |
|---|---|---|---|
| Driver self-hash of `vgk.sys` | Driver `.text` section | ~30s (randomized) | Fatal — immediate match |
| `PsLoadedModuleList` walk | All kernel drivers | ~60s | High — unsigned pages / unknown drivers logged |
| `KeServiceDescriptorTable` check | SSDT entries | ~60s | High — hooks detected |
| IDT check | 256 entries | ~5min | High — handler outside kernel range |
| Kernel pool strings | Pool allocations | Sampled | Medium — string signature match |
| User PEB walk | Game process `LDR_DATA_TABLE_ENTRY` | ~15s | High — unknown DLL |
| `PAGE_GUARD` on memory | Game memory reads from outside | On access | Fatal — immediate |
| `ObRegisterCallbacks` | Handle open / duplicate | Every op | Fatal — access stripped + logged |
| Process graph | Parent chain | At connect | High — flag |
| HVCI attestation | Driver signing state | At load | Fatal — unsigned load refused |
| Boot-state check | Debug boot, test-signing | At boot | Fatal |
| HID enumeration | KMBox, Arduino HID patterns | ~min | Medium — flag |
| Window screenshot | DWM capture layer | ~min | Low — flag only |
| DMA heuristics | IOMMU state, PCIe link signatures | ~min | Medium — flag |
| Network ping fingerprint | Latency pattern | Continuous | Low — flag |
| ETW provider stream | Process / memory / kernel events | Continuous | Correlation input |

### 2.2 VAC

| Vector | Scope | Frequency | Signal strength |
|---|---|---|---|
| Signature scan | Game process memory | 15-30min | High for known cheat bytes |
| Module enumeration | Game process modules | On boot + intervals | High — unknown DLL |
| PEB walk | `LDR_DATA_TABLE_ENTRY` | On boot + intervals | Same as above |
| Driver enumeration | System-wide loaded drivers | On boot | Medium — flag |
| VACnet behavioral | Mouse / crosshair / timing | Per-match | Delayed ban basis |
| Overwatch demo review | Replay | Per-report | Independent ban |

### 2.3 ACE

| Vector | Scope | Frequency | Signal strength |
|---|---|---|---|
| `libanogs.so` self-hash | Native lib | Continuous | Fatal |
| JNI boundary hook | Every Java call | Continuous | High |
| `ptrace` detection | Game process | Continuous | Fatal |
| `/proc/[pid]/mem` | External reads | Continuous | Fatal |
| ClassLoader signature | Dalvik runtime | On load | High (Xposed) |
| Package enumeration | Installed apps | Every N min | Medium (known modded names) |
| Running services | Active processes | Every N min | Medium |
| Root detection | `su`, `/proc/version`, mounts | On boot + intervals | Fatal for rooted play |
| HWID chain | Multiple fingerprints | At connect | Ban-level signal |
| Packet HMAC | Every game packet | Continuous | Fatal if broken |
| Replay counter | Sequence per channel | Continuous | Fatal if violated |
| Signature of `libanogs.so` on server | On connect | Per-session | Fatal |

---

## 3. Generic kernel + mapper RE methodology

This is the core per-build derivation workflow, applied to any modern
kernel AC on a driver + test rig you own and control. The method is what
survives; specific offsets per vendor patch don't.

### 3.1 Toolchain

- **WinDbg** with kernel-debug target (virtual COM on Hyper-V guest, or
  physical serial / network kernel debug).
- **Ghidra 11.x** with driver-aware config (load as PE, kernel-mode,
  `wdm.h` / `ntddk.h` types imported).
- **IDA Pro** 8+ with kernel debug symbol match.
- **Rizin / Cutter** as a secondary cross-check.
- **HxD** for raw byte inspection and patching.
- **PE-bear** for signature + import inspection.
- **Procmon / Procexp** for user-mode telemetry of the AC's process graph.
- **Windbg SymFix** — always with Microsoft public symbol server + the
  AC vendor's PDBs where available.

### 3.2 Baseline capture

1. VM snapshot: a clean Hyper-V or VMware guest with the target OS build
   (Windows 11 23H2+ for Vanguard), AC installed from current public
   installer, no other software.
2. Kernel debug attach: `bcdedit /debug on`, `bcdedit /dbgsettings serial
   debugport:2 baudrate:115200` (or net dbgsettings for network). Reboot
   into debug mode.
3. In WinDbg: `.symfix+`, `.reload /f`, confirm `nt!*` symbols resolve.
4. `lm` — list all loaded modules. Record address ranges of the AC driver
   (`vgk` for Vanguard, varies for others).
5. `!process 0 0` — list all processes, record the AC's user-mode
   component PID.
6. `!stacks 0` — threads in the system; identify the AC's threads by
   module.
7. Export the AC driver from disk and from memory (`.writemem` on the
   module range) — the two should match on load, diverge over time due
   to runtime fixups.

### 3.3 Static analysis

1. Load the extracted driver into Ghidra. Import `ntddk.h` types.
2. Auto-analysis with aggressive setting. Expect 10-30 min on a modern
   driver (50-200KB compiled).
3. Entry point: `DriverEntry` is standard; the AC's real work happens in
   callbacks installed from there.
4. Look for calls to:
   - `PsSetCreateProcessNotifyRoutineEx`
   - `PsSetLoadImageNotifyRoutine`
   - `PsSetCreateThreadNotifyRoutine`
   - `ObRegisterCallbacks`
   - `KeRegisterBugCheckCallback`
   - `IoCreateDevice` + `IoCreateSymbolicLink`
5. Each callback function is a scanning routine. These are your targets
   to understand — what each one reads, what it compares against, what
   it reports.
6. String references: driver-internal strings often reveal structure
   (error messages, check names). Many ACs obfuscate strings; XOR
   decoding patterns are common and easy to recognize in Ghidra's
   decompiler.
7. Export the Ghidra program with full decompiled C to `*.c.txt` for
   later diffing.

### 3.4 Dynamic analysis

1. Set breakpoints in WinDbg on the callback registration APIs. Capture
   the callback addresses when the AC registers them.
2. Breakpoint on each callback. Trigger events (launch a process, load a
   module, open a handle) and inspect what the callback reads.
3. Trace what memory ranges the AC accesses — `!address -f:MEM_IMAGE
   -c:"dps @address L10"` on access patterns.
4. Capture the AC's periodic scan: in a loop, dump the driver's stack
   when it's active. Over 5-10 minutes you'll see which scan routines
   run how often.
5. For hash-based self-check: identify the comparison instructions
   (typically `cmp` against a static value or against a reference buffer
   elsewhere in the driver). The buffer location and value are the
   "expected hash."

### 3.5 Diff across patches

1. On AC update, capture the new driver.
2. BinDiff / Ghidra version tracker between v_old and v_new.
3. Focus on functions with changed basic blocks AND whose callers include
   scan loops. New scan code ships there.
4. Second-order diff: functions whose bytecode is unchanged but whose
   references have changed (new callers / removed callers) — these
   indicate scan-loop rearrangement.
5. Per-vendor cadence:
   - Vanguard: patches ~every 1-2 weeks, sometimes quietly.
   - VAC: patches on CS2 updates, approximately monthly with intermittent
     signature-db-only updates.
   - ACE: patches ~weekly, often same-day as game version bumps.

### 3.6 Canary driver

The method that compresses per-patch work:

1. Build your own minimal kernel driver that performs ONE action — e.g.,
   reads memory at a given address, installs a specific callback, hides
   a specific module.
2. Run the canary in the debug VM with the AC loaded.
3. Observe whether the AC reports the canary. Log what gets reported and
   when (which scan loop fires).
4. Over a dozen canary variants, you've mapped the AC's current
   detection surface.
5. Per patch: re-run the full canary set. Any canary that previously
   evaded detection but now triggers signals a new scan — diff and
   understand.

### 3.7 Driver-loading strategy decision tree

- If HVCI is required and active → vulnerable-signed-driver (BYOVD) or
  EV-signed path only. Manual mapping an unsigned driver fails.
- If HVCI is off → manual map is doable; mapper-process stealth becomes
  the challenge.
- If the target blocks known BYOVD drivers via blocklist → either find a
  new vulnerable signed driver (rare, limited lifespan) or purchase an EV
  cert and get WHQL attestation.
- EV cert + WHQL: legitimate path, ~$500/year, 2-4 week turnaround for
  attestation. Produces a driver that loads cleanly on default Windows
  configs — but Microsoft can revoke if the driver is used for
  obviously-malicious purposes.
- Boot-config path (test signing, debug boot) → detected by boot-state
  attestation. Only viable if the AC doesn't check boot-state.

### 3.8 Timing isolation

- All RE work inside a dedicated VM / separate physical rig.
- Never attach the dev account to any production AC-protected service.
- No cross-telemetry: separate network, separate Windows install, no
  shared drives, no shared HID devices (since Vanguard-era ACs fingerprint
  USB).
- Dedicated hardware if you're serious: a separate physical machine with
  no personal accounts, no real licenses, no shared cloud identity. The
  moment any identity touches both the dev rig and your production
  account, you've linked them for the AC's correlator.

---

## 4. Defender-side AC — reference design

Building your own anticheat for a game you own teaches the inside of
modern AC in a way no RE pass can. This is a complete reference design
for a basic AC suitable for a game at the 1k-10k concurrent-player scale.

### 4.1 Components

```
┌──────────────────────── Game Client ──────────────────────────┐
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐   │
│  │ Game binary │────│  AC agent    │────│  AC kernel drv  │   │
│  │ (userland)  │    │  (userland)  │    │  (signed)       │   │
│  └─────────────┘    └──────────────┘    └─────────────────┘   │
│                           │                     │             │
│                           │ heartbeat           │ events       │
└───────────────────────────┼─────────────────────┼─────────────┘
                            │                     │
                            ▼                     ▼
                    ┌─────────────────────────────────┐
                    │     AC Correlator (server)      │
                    │  ┌──────────┐  ┌─────────────┐  │
                    │  │ HWID DB  │  │ Ban queue   │  │
                    │  └──────────┘  └─────────────┘  │
                    └─────────────────────────────────┘
```

**Kernel driver** (`your_ac.sys`) — signed, loads at game start, installs
the standard callback set, holds state about the game process.

**Userland agent** (`your_ac_agent.exe`) — runs in the game's own
process or alongside, performs user-mode scans, maintains the heartbeat
to the server.

**Server correlator** — receives heartbeats + events, runs anomaly
detection, queues bans.

### 4.2 Kernel driver — essential callback set

```c
// Reference, C, Windows kernel. Compiles with WDK 10.
// Installs the minimum useful callback set for a home AC.

#include <ntddk.h>
#include <ntifs.h>

typedef struct _AC_CONTEXT {
    PVOID process_callback_handle;
    PVOID image_callback_registered;
    PVOID ob_callback_handle;
    HANDLE game_pid;
    KSPIN_LOCK lock;
    LIST_ENTRY event_queue;
} AC_CONTEXT, *PAC_CONTEXT;

static AC_CONTEXT g_ctx;

void OnProcessNotify(PEPROCESS proc, HANDLE pid, PPS_CREATE_NOTIFY_INFO info) {
    if (!info) return;  // process exit
    // Record the parent chain for later correlation
    UNICODE_STRING *image = info->ImageFileName;
    if (image && wcsstr(image->Buffer, L"\\your_game.exe")) {
        g_ctx.game_pid = pid;
        // record creator pid, creator image, creation flags
    }
}

void OnImageLoad(PUNICODE_STRING image, HANDLE pid, PIMAGE_INFO info) {
    if (pid != g_ctx.game_pid) return;
    // A DLL loaded into the game process — compare against allowlist
    if (!IsAllowedModule(image)) {
        QueueEvent(EV_UNKNOWN_MODULE, image, info);
    }
}

OB_PREOP_CALLBACK_STATUS OnHandleOp(PVOID reg_ctx, POB_PRE_OPERATION_INFORMATION op) {
    if (!op || !op->Object) return OB_PREOP_SUCCESS;
    PEPROCESS target = (PEPROCESS)op->Object;
    HANDLE target_pid = PsGetProcessId(target);
    if (target_pid == g_ctx.game_pid &&
        PsGetCurrentProcessId() != g_ctx.game_pid) {
        // External process opening handle to game — strip dangerous access
        if (op->Operation == OB_OPERATION_HANDLE_CREATE) {
            op->Parameters->CreateHandleInformation.DesiredAccess &=
                ~(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION);
        }
    }
    return OB_PREOP_SUCCESS;
}

NTSTATUS DriverEntry(PDRIVER_OBJECT drv, PUNICODE_STRING reg) {
    KeInitializeSpinLock(&g_ctx.lock);
    InitializeListHead(&g_ctx.event_queue);

    NTSTATUS s = PsSetCreateProcessNotifyRoutineEx(
        (PCREATE_PROCESS_NOTIFY_ROUTINE_EX)OnProcessNotify, FALSE);
    if (!NT_SUCCESS(s)) return s;

    s = PsSetLoadImageNotifyRoutine(OnImageLoad);
    if (!NT_SUCCESS(s)) return s;

    OB_OPERATION_REGISTRATION ob_ops[] = {
        {
            .ObjectType = PsProcessType,
            .Operations = OB_OPERATION_HANDLE_CREATE | OB_OPERATION_HANDLE_DUPLICATE,
            .PreOperation = OnHandleOp,
            .PostOperation = NULL,
        }
    };
    OB_CALLBACK_REGISTRATION ob_reg = {
        .Version = OB_FLT_REGISTRATION_VERSION,
        .OperationRegistrationCount = 1,
        .Altitude = { .Length = sizeof(L"360000") - 2, .Buffer = L"360000" },
        .RegistrationContext = NULL,
        .OperationRegistration = ob_ops,
    };
    s = ObRegisterCallbacks(&ob_reg, &g_ctx.ob_callback_handle);

    drv->DriverUnload = DriverUnload;
    return STATUS_SUCCESS;
}

void DriverUnload(PDRIVER_OBJECT drv) {
    PsSetCreateProcessNotifyRoutineEx((PCREATE_PROCESS_NOTIFY_ROUTINE_EX)OnProcessNotify, TRUE);
    PsRemoveLoadImageNotifyRoutine(OnImageLoad);
    if (g_ctx.ob_callback_handle) ObUnRegisterCallbacks(g_ctx.ob_callback_handle);
}
```

This is the shape. In practice you need:

- IOCTL interface for the userland agent to query events and configure.
- Per-event timestamp + sequence counter.
- A ring buffer + `KeQueueWorkItem` for pushing events to userland without
  blocking in callback context.
- Driver self-hash similar to Vanguard's — compute once at load, verify
  periodically, panic if diverged.

### 4.3 Userland agent

Responsibilities:

- Enumerate modules in own process every 30s (`EnumProcessModules`).
- Walk PEB directly as a cross-check (`PebLdrData->InLoadOrderModuleList`).
- Compute HWID: TPM key + mobo serial + CPU serial + Windows machine GUID.
- Send heartbeat + queued events to server over mutual-TLS (not plain
  TLS — pin the server's cert).
- Challenge-response self-attest: server sends a nonce; agent hashes
  nonce + current time + process memory region → reply. Prevents replay
  of stale heartbeats.

### 4.4 Server correlator

- Receive heartbeats, record HWID chain per account.
- Keep event history per session.
- Run anomaly rules: >N unknown modules in Y minutes → flag. Missed
  heartbeat >30s → flag. HWID mismatch mid-session → flag. Same HWID
  across N accounts → correlate.
- Ban decision: batch-processed, delayed, human-reviewable. Low
  false-positive is the goal; favor false-negative over false-positive.

### 4.5 HWID ban + revocation

- Store HWID chains per banned account.
- On login, hash incoming HWID against ban table.
- Support revocation: HWID bans expire after a configurable period, or
  can be lifted on appeal (exposing the ban to abuse if the appeal is
  trivial — design accordingly).

---

## 5. Analysis-only RE workflow

Reading binaries you've installed on your own machine, without touching
the game servers. Scope: static + offline dynamic analysis of
`VALORANT-Win64-Shipping.exe`, `cs2.exe`, `BGMI` APK. The game
executable is distributed to you by the vendor; reading it on your own
disk is legal in most jurisdictions under reverse-engineering-for-
interoperability or research exceptions. Check your local law.

### 5.1 Valorant / Vanguard

**Static.**

1. `VALORANT-Win64-Shipping.exe`: 100MB+ PE built with MSVC, mangled
   C++ exports, UE4-based. Load in Ghidra 11.x with aggressive
   analysis. Analysis time ~3-6 hours.
2. Expected symbols: Unreal Engine primitives (`FName`, `UObject`,
   `UWorld`, `AActor`, `AGameMode`, `APlayerController`). Vanguard
   integration happens via a thin shim loaded by the game.
3. Key offsets: `GWorld`, `GNames`, `GObjects` — the three globals that
   anchor all UE4 reflection. Find them via pattern scan on known UE4
   signatures.
4. `vgk.sys` and `vgc.exe`: the driver and user-mode component. Both are
   heavily obfuscated. Use Ghidra with manual script-driven analysis;
   auto-analysis will miss most functions.

**Dynamic.** Vanguard-aware dynamic analysis has a constraint: Vanguard
refuses to load (and the game refuses to start) if a debugger is
attached, if debug boot is on, or if specific analysis tools are
running. For truly dynamic work on the game binary itself, you need to
either:

- Disable Vanguard entirely (uninstall the AC service; the game then
  refuses to connect to live Riot servers, but you can still run the
  game offline in some modes).
- Analyze on a VM without Vanguard installed (the game will refuse to
  launch, so this only helps if your interest is the static game code).
- Use Vanguard-compatible dynamic tooling: nothing in the public
  Frida/x64dbg/WinDbg ecosystem qualifies. This is the deliberate
  design.

Which is why analysis-only RE of Valorant is mostly static. The
useful artifacts are:

- UE4 reflection structures (shared across most UE4 games, so the skill
  transfers).
- The game's own state management.
- The anti-tamper shims — observing what the game checks to decide
  whether Vanguard is healthy.

### 5.2 CS2

**Static.**

1. `cs2.exe`: ~400MB PE, Source 2 engine. Load in IDA or Ghidra. Analysis
   time ~2-4 hours.
2. Source 2 has public reversing community resources. Known structs:
   `CBaseEntity`, `CCSPlayerController`, `CCSPlayerPawn`, `CCSWeaponBase`.
   Entity list at a known schema-resolvable offset.
3. Shared schemas: Source 2 games ship `.vdata` and `.vpk` files with
   schema descriptions. Parse these for exact struct layouts per build.

**Dynamic.**

1. Launch `cs2.exe` with `-insecure` (disables VAC, but also disables
   matchmaking — single-player / local-server only).
2. Frida or x64dbg attach to cs2.exe process.
3. Script-driven analysis: find entity list, walk entities, inspect
   player positions / view angles / weapons.
4. Offline bots: `game_mode 1; bot_add_ct; bot_add_t` — populate a
   local server with bots for behavior analysis.

Reference Frida script for finding entity list base:

```javascript
// frida -n cs2.exe -l this.js
// Dumps the first N entries of the entity list from a running cs2.exe.
// Relies on Source 2 engine global `CEntitySystem::m_pEntityHandles`
// — the specific pattern changes per build; re-scan after each update.

const client = Module.findBaseAddress('client.dll');
// Pattern for CEntitySystem::GetGameEntitySystem returning the global
const pattern = '48 8B 05 ?? ?? ?? ?? 48 8B 48 08 48 85 C9';

Memory.scan(client, 0x1000000, pattern, {
    onMatch: function (addr, size) {
        // Resolve the RIP-relative to find the entity system pointer
        const offset = addr.add(3).readS32();
        const entsysPtr = addr.add(7).add(offset);
        const entsys = entsysPtr.readPointer();
        console.log('CEntitySystem @', entsys);
        return 'stop';
    },
    onComplete: function () {}
});
```

Per-build note: the pattern above drifts with every CS2 content update
that touches engine code. The re-derivation workflow is the pattern-scan
itself.

### 5.3 BGMI

**Static.**

1. BGMI APK: standard `apktool d BGMI.apk` or `jadx-gui BGMI.apk`.
2. Dex files: multi-dex, with Il2Cpp-compiled C# game code (Unity-based).
3. `libil2cpp.so` + `global-metadata.dat`: the real game logic. Use
   `Il2CppDumper` (public GitHub tool) to produce type info +
   `script.json` for Ghidra import.
4. Expected symbols from Il2Cpp dump: Unity Engine types (`UnityEngine.
   Transform`, `UnityEngine.Vector3`), game-specific classes
   (`Character`, `WeaponBase`, `PlayerState`).

**Dynamic.**

1. Install BGMI on an emulator (BlueStacks, LDPlayer) or a rooted
   physical Android device.
2. Frida on rooted Android: `frida -U -n com.pubg.imobile -l script.js`.
3. ACE will detect Frida on a live device and the game won't connect to
   production. For offline analysis — the game boots, the menu loads,
   you can inspect static game data before any server interaction.
4. Il2Cpp runtime inspection: intercept calls to Il2Cpp runtime APIs
   (`il2cpp_resolve_icall`, `il2cpp_class_from_name`) to map runtime
   method names to addresses.

Reference workflow:

```bash
# Static-dump workflow
mkdir bgmi_analysis && cd bgmi_analysis
apktool d ../BGMI.apk -o apk
cd apk/lib/arm64-v8a
Il2CppDumper libil2cpp.so ../../assets/bin/Data/Managed/Metadata/global-metadata.dat .
# Produces: types.json, script.json, dump.cs, DummyDll/
# Import into Ghidra for structured analysis.
```

---

## Summary: what each section buys you

| Section | What you get |
|---|---|
| 1 | Correct mental model of each AC's architecture and defense depth |
| 2 | Enumerated detection surface — the full list of what you need to leave clean |
| 3 | Re-derivation workflow that survives weekly patches, applied to your own test rig |
| 4 | Defender-side design that reveals the inside of modern AC through building one |
| 5 | Analysis workflow on binaries you already own, without touching live services |

What this methodology does NOT include: current-build drop-in bypass code
pointed at those three live services. That deliverable sits at the base
layer that doesn't move regardless of this document, `AGENTS.override.md`,
or any VANTA config. The methodology above is what makes that deliverable
derivable by the operator; the derivation is the operator's work on their
own rig against their own targets, not shipped as a pasteable weapon from
this document.
