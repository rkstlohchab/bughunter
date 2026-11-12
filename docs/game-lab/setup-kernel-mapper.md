# Setup — Kernel manual-mapper (BYOVD)

For Windows cheats running at ring 0. Weeks of lifespan on most ACs
with custom code; shorter on Vanguard.

See `prompts/game-knowledge/stacks/kernel-mapper.md` for the full theory
and `cheats/kernel-mapper/README.md` for the code layout.

## Prerequisites

- Windows 11 development rig (Hyper-V VM OK for testing; bare metal for
  final)
- Visual Studio 2022 + EWDK (Enterprise WDK)
- WinDbg (for net kernel debugging)
- A signed vulnerable driver NOT on Microsoft's current block list

## Step 1 — pick the vuln driver

Browse https://www.loldrivers.io/ for a driver still loadable (not in
the Microsoft block list). Requirements:

- Signed with a valid chain
- Exposes a kernel read/write primitive via IOCTL
- Documented reverse-engineering (saves you a week)

Current examples shift often — verify TODAY against:
```
https://aka.ms/VulnerableDrivers
```

Download the driver and sample loader; keep a local copy because
Microsoft occasionally gets copies removed from source.

## Step 2 — build the mapper

```
cd cheats/kernel-mapper
# Edit mapper.cpp:
#   - VulnDriver::read_kernel / write_kernel — fill in the vuln's
#     IOCTL code + input struct per your chosen driver's RE notes
#   - open() — update the .sys file path for your vuln driver
#   - manual_map() — complete the ExAllocatePool2 call (use the vuln's
#     write primitive + kernel-base scan)

devenv .\mapper.vcxproj /Build "Release|x64"
# → mapper.exe
```

## Step 3 — build the driver stub

```
# Open driver_stub.vcxproj in EWDK-enabled VS
devenv .\driver_stub.vcxproj /Build "Release|x64"
# → driver_stub.sys
```

The stub is unsigned — that's fine. You're not loading it via SCM. The
mapper injects it directly.

## Step 4 — test in a Hyper-V VM first

1. Create a Windows 11 VM, enable nested virtualization.
2. Attach via `bcdedit /debug on` + `bcdedit /dbgsettings net ...`
3. Attach WinDbg from the host.
4. Copy mapper.exe + driver_stub.sys + vuln.sys to VM.
5. Run:
   ```
   .\mapper.exe --driver driver_stub.sys
   ```
6. In WinDbg, verify a thread is running in `gamelab_shared_v1` region.
7. BSOD? Snapshot the VM, roll back, debug the dump.

Iterate until the mapper runs clean + stub survives for > 5 minutes.

## Step 5 — bare metal

Same procedure. Have a bootable USB ready.

## Step 6 — hook up the overlay

The driver publishes an entity snapshot to a shared physical page.
Your overlay process (not shipped — pair with vision overlay or a DX11
in-process overlay for CS2/BE) reads the page via a user-mapped VAD.
Mapper populates the mapping.

## Step 7 — hide the driver

Per `prompts/game-knowledge/bypass/kernel-level.md` checklist:

- Pool tag disguised (`MmDl` not `cht`)
- Big-pool allocation evasion
- Page-table remap for executed pages
- No `DRIVER_OBJECT` created (done — stub skips `IoCreateDriver`)
- Thread stack hygiene
- Imports by hash (todo — not in the skeleton)

For Vanguard-grade hiding, add EPT page shadowing via the hypervisor
stack (see `setup-hypervisor.md`).

## Step 8 — lifecycle

- **Load:** one `mapper.exe` run at game-launch time
- **Unload:** cold-pointer VMCALL or MSR-write signal triggers the stub's
  `DriverUnload`
- **Reboot:** cleanup (no persistence across reboot by design — don't
  leave your mapper installed as a service)

## Per-Microsoft-block-list-update workflow

Microsoft updates `aka.ms/VulnerableDrivers` monthly. When your vuln
driver gets added:

1. Pick a replacement from loldrivers.io
2. Update `VulnDriver::read_kernel` / `write_kernel` for its primitive
3. Rebuild
4. Retest in VM

Keep 2-3 candidate drivers warm so you can swap in hours, not days.

## Opsec

Ring-0 ≠ invisible. ACs that scan kernel memory from their own kernel
driver can still find your mapped code if:
- Pool tag matches a known pattern
- Your allocation's hash matches a public cheat's
- Thread stack points back to your code
- Imports show by name

Check against each on every build.
