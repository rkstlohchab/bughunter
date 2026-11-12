# Kernel manual-mapper — reference skeleton

Build a BYOVD (Bring Your Own Vulnerable Driver) manual-mapper for ring-0
cheat loading on Windows. This directory ships a skeleton with the shape,
not a signed driver with an active vulnerability — those shift monthly and
MUST be verified against the current Microsoft driver block list
(https://aka.ms/VulnerableDrivers) before use.

## Files

- `mapper.cpp` — usermode loader. Enumerates a signed+vulnerable driver's
  IOCTL surface, uses an arbitrary-kernel-write primitive to allocate pool,
  copy sections, resolve imports+relocs, and call `DriverEntry`.
- `driver_stub.cpp` — the unsigned kernel driver that gets mapped. Reads
  game memory from ring-0, publishes to usermode via a shared section.
- `signatures/` — IDA-style signatures per game for pointer-chain discovery
  from ring-0 (scoped per-game as the DMA stack is scoped).

## The missing piece: the vuln driver

This repo does not ship a vulnerable driver. Build order:

1. Pick a signed driver not on the current Microsoft block list that has a
   known arbitrary-read/write vuln. Public tracking: https://www.loldrivers.io/
2. Document the IOCTL code + input buffer layout that triggers the primitive.
3. Plug it into `mapper.cpp` at `VulnDriver::write_kernel()` /
   `VulnDriver::read_kernel()`.

## Build

```powershell
# EWDK (Enterprise Windows Driver Kit) for the kernel driver
devenv .\driver_stub.vcxproj /Build "Release|x64"

# MSVC for the mapper (usermode)
devenv .\mapper.vcxproj /Build "Release|x64"
```

## Load

```powershell
# As admin:
sc create vulnsvc type= kernel start= demand binPath= "C:\path\to\vuln.sys"
sc start vulnsvc
# Mapper now has a handle to the vuln driver's device object.
.\mapper.exe --driver driver_stub.sys --game cs2
# Mapper writes pool, copies driver, calls DriverEntry, deletes service.
sc delete vulnsvc
```

## Hiding

See `prompts/game-knowledge/bypass/kernel-level.md` for:
- Pool tag disguise
- Big-pool evasion
- Page-table RW→RX flipping
- Thread stack hygiene
- Hashed import resolution
- No `DRIVER_OBJECT` creation

## Communication with overlay

`driver_stub.cpp` creates a 1MB shared physical page, maps it into a chosen
usermode process (your ESP overlay) by walking its VAD. The overlay reads
`gamelab_shared_v1` magic + entity-list snapshot from the page.

## Signed-driver cert check

```powershell
signtool verify /v /pa .\vuln.sys
# Confirm signer is in the Windows kernel-driver signing chain and NOT
# on the block list.
```

## Current environment caveats (2026)

- HVCI + KMCI block unsigned kernel code by default on Win11. BYOVD still
  works via the vuln driver's own signed surface, but Microsoft's block
  list evicts known vuln drivers monthly.
- VBS adds another layer; your mapper must handle VBS-on.
- Vanguard, EAC, BE: each performs independent kernel-pool scans and
  module walks. See `prompts/game-knowledge/anticheat/*`.

## When to NOT do this

- If you have $200 for DMA, skip this. DMA is invisible to every kernel AC
  without BSOD risk or Microsoft-block-list racing.
- For CS2 / older BE titles, kernel mapper is reasonable (lower-aggression
  AC). For Vanguard, DMA + behavioral opsec is the dominant stack.
