---
title: Kernel-level bypass — ring-0 strategies vs Vanguard/BE/EAC
tags: [kernel, ring-0, byovd, mapper]
source: in-house
updated: 2026-11-12
lifespan_class: weeks
---

# Kernel-level bypass

How to run cheat code at ring 0 against modern AC. For the full stack see `stacks/kernel-mapper.md`.

## The three primitives you need

1. **Load code into kernel** — either signed (your own EV cert, not realistic) or via BYOVD arbitrary write
2. **Hide from pool / module walks** — allocation tag disguise, remap into non-pool ranges, EPT page shadowing
3. **Communicate with usermode** — shared section, side-channel

## BYOVD loader patterns

Pattern A — Direct kernel-write primitive:
- Vuln driver exposes `MmCopyMemory`-like IOCTL
- You use it to overwrite `ntoskrnl!HalpAllocateAdapterChannel` with a jump to your shellcode
- Shellcode allocates pool, copies your driver, jumps to your `DriverEntry`

Pattern B — SMM / UEFI load:
- Not really BYOVD — you install a UEFI driver at install-time
- Runs before Windows, has full MMIO access
- More durable, more destructive-on-mistake

Pattern C — Kernel DSE bypass:
- Hook `CiValidateImageHeader` to return "valid" for your signed-but-tampered driver
- Requires kernel-write primitive to patch the code
- MS has patched the exploitable ones recently

## Hiding kernel presence

Vanguard's pool scan is the main threat. Techniques:

### Allocation disguise

```c
// Instead of:
PVOID buf = ExAllocatePool2(POOL_FLAG_NON_PAGED, size, 'cht ');
// Use:
PVOID buf = ExAllocatePool2(POOL_FLAG_NON_PAGED, size, 'MmDl');  // disk driver tag
// AC scanners that flag unusual tags miss this
```

### Big-pool evasion

Allocations ≥ `PAGE_SIZE` go into `Big Pool`, which has its own tracking table that AC can enumerate via `RtlpBigPoolTable`. Keep allocations under 4KB, chain them.

### Page-table remap

Allocate pool RW. At runtime, for the page containing your executable code, patch the PTE's `NX` bit to allow execute. This leaves the allocation looking "data" when AC checks page protections, while still executing.

Even better — remap physical frames into a different virtual address outside the pool range. Requires self-reference PML4 entry manipulation.

### EPT shadowing (hypervisor-assist)

If you have a hypervisor underneath, use EPT to show AC a different page than what executes:

- Read access: EPT maps virt→phys(original_bytes)
- Execute access: EPT maps virt→phys(hooked_bytes)

AC reads original; CPU executes hooked. Undetectable to any software-only AC scan.

See `stacks/hypervisor.md` for the HV foundation.

## Callback registration

Don't register for `PsSetCreateProcessNotifyRoutine` etc. — AC walks the callback array. Hook existing callbacks if you need process-create signal.

Alternative: APC injection into an existing kernel thread; no new callback entry.

## Driver object avoidance

Don't call `IoCreateDriver`. Don't fill in a `DRIVER_OBJECT`. Don't register a dispatch table. All three are walked by AC.

Your driver should be pure code at an allocation, called from a kernel thread. For usermode comm, use a shared section mapped into a specific usermode process's VAD.

## Communication side-channels

| Channel | AC detection |
|---|---|
| `\Device\<name>` IOCTL | Easy — enumerate device objects |
| `\\.\Pipe\<name>` | Enumerable |
| Shared section via `ZwOpenSection` | Section name enumerable |
| Shared physical page mapped into both processes | Harder to detect |
| Timer DPC writing to a known usermode page | Side-channel via MMIO-like pattern |
| HID device emits magic values your overlay reads | Works but complex |

Best: shared physical page + a mutex in the shared page. AC doesn't enumerate shared physical pages.

## Thread stack hygiene

Any work your kernel code does on a thread leaves a stack frame. AC that walks `KTHREAD`s and checks `StackBase/StackLimit` for pointers into non-whitelisted regions catches you.

Clean up after yourself:
- Run in APC context where possible, not in dedicated kernel thread
- Zero the stack after use
- Use short-lived DPCs that return before AC's thread walk ticks

## Symbol resolution

Don't import kernel functions by name — `MiSnapThunk` scans imports. Resolve at runtime:

```c
// hash of the function name
#define HASH_ExAllocatePool2 0x4fe6a54c

PVOID resolve_by_hash(ULONG hash) {
    // walk ntoskrnl export table manually, hash each name, compare
    ...
}

typedef PVOID (*pExAllocatePool2)(POOL_FLAGS, SIZE_T, ULONG);
pExAllocatePool2 ExAllocatePool2 = resolve_by_hash(HASH_ExAllocatePool2);
```

## PatchGuard

Microsoft's PatchGuard (KPP) kills machines that patch selected kernel structures (SSDT, IDT, select critical globals). Don't patch anything KPP covers; your work goes in your own allocation.

Hooks on exported functions via `MmGetSystemRoutineAddress` are not KPP-covered. Patch the first few bytes of a target function to jump to your trampoline; KPP doesn't see.

## AC-specific notes

### Vanguard

- Boots first. You either HV-under or you don't run.
- Pool scan every 10-30s. Disguise + EPT shadow needed.
- `vgk.sys` registers its own APC on every thread — doesn't attack yours, but can be leveraged for timing sync.

### BattlEye

- Looser. Pool tag check less aggressive than Vanguard.
- Signature scan of game process memory from usermode — your kernel code is fine as long as nothing you wrote lives inside the game process.

### EAC

- Similar to BE. Weaker on Linux (usermode only on Proton builds).

## Testing in a VM

Build and iterate in a Hyper-V VM:
- Checked build of Windows for `KdPrint` output
- WinDbg attached via net kernel debug
- Snapshot before each mapper test; roll back on BSOD

Only test on a bare-metal rig after the VM says clean. BSODs on your main machine eat hours of setup.
