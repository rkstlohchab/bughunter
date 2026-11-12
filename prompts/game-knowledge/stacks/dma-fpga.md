---
title: DMA (PCILeech + MemProcFS + KMBox) stack
tags: [dma, pcileech, memprocfs, kmbox, fpga, universal, ring-neg-1]
source: in-house
updated: 2026-11-12
lifespan_class: invisible
---

# DMA — direct memory access from a second PC

PCI Express allows a device to read and write host memory without the CPU's involvement (DMA). A PCIe FPGA card in the gaming PC, controlled over USB from a second PC, reads the game's memory directly. The gaming PC's OS and anticheat never see the read happen — the CPU wasn't involved.

Combined with KMBox Net (a hardware mouse injector that splices into a real USB mouse's HID stream), the gaming PC runs a vanilla Windows install with vanilla Valorant and vanilla Vanguard, and the cheat runs entirely on the second PC + FPGA.

## Hardware

| Part | Options | Price (2026) |
|---|---|---|
| FPGA card | CaptainDMA Squirrel (PCILeech-compatible) | ~$180 |
| | LeetDMA / "ONE" | ~$200 |
| | Enigma X1 / FBPCIE | ~$250 |
| | Older ScreamerPCIe / PCILeech 75T | ~$250 (used) |
| USB-C → USB-A cable | Data cable, not power-only | $5 |
| Second PC | Any modern laptop (reader is CPU-light) | — |
| KMBox Net | KMBox B+/B Pro | ~$50 |
| Real mouse | Any — splices through KMBox | — |

The FPGA goes into a PCIe slot on the gaming PC (x1 is enough for reads). It is sold as a "network capture" or "memory analysis" card — PCILeech is the open-source firmware that makes it usable.

## Software (second PC)

- [PCILeech](https://github.com/ufrisk/pcileech) — command-line USB-to-FPGA bridge
- [MemProcFS](https://github.com/ufrisk/MemProcFS) — mounts gaming-PC memory as a FUSE filesystem on the reader PC
- `vmmpy` — Python bindings for MemProcFS (`pip install memprocfs` on the reader)
- KMBox Net Python SDK (bundled with device)

## Pipeline

```
Second PC ──USB──> FPGA card ──PCIe──> Gaming PC's RAM
                                              │
                                              │ (reads bypass CPU)
                                              v
                                       Vanguard sees nothing
```

```
Second PC: PCILeech → MemProcFS → vmmpy → game pointers → aim delta
                                                                │
                                                                v
                                                     KMBox Net API ──LAN──> KMBox B+
                                                                                │
                                                                                v
                                                                    USB HID report splice
                                                                                │
                                                                                v
                                                                         Gaming PC USB
```

## Memory reading

MemProcFS mounts the gaming PC's physical memory at `/M/` (or `M:\` on Windows). Access patterns:

```python
import memprocfs

vmm = memprocfs.Vmm(["-device", "fpga://"])
procs = vmm.process_list()
for p in procs:
    if p.name.lower() == "valorant-win64-shipping.exe":
        base = p.module("valorant-win64-shipping.exe").base
        # walk pointer chain
        addr = base + 0x08B23C78    # offset derived per build
        for off in (0x30, 0x28, 0x0, 0x10):
            addr = vmm.memory.read_u64(p.pid, addr)
            addr += off
        health = vmm.memory.read_u32(p.pid, addr)
        print(health)
```

Read throughput on a Squirrel: ≈ 100 MB/s. More than enough for per-frame entity-list walks.

## Offset derivation per build

- Dump game executable with MemProcFS (`cat /M/{pid}/dll/valorant-win64-shipping.exe > /tmp/val.exe`)
- Sig-scan in IDA/Ghidra for known patterns (world-to-screen fn, entity list fn)
- Patch new offsets into a `offsets.json` file
- GameLab has `game_offsets.refresh` RPC that automates per-build re-derivation when a signature set is already registered

## KMBox input splice

KMBox B+ / Pro connects between your real mouse and the gaming PC. It presents as that mouse (same VID/PID) to the gaming PC and splices in your injected deltas. API is a JSON-RPC over LAN.

```python
from kmbox_api import KmBox

km = KmBox(ip="192.168.1.50", port=65432)
km.mouse_move(dx=42, dy=-13)
km.mouse_click("left")
```

The KMBox hardware is undetectable by any software means — it's another USB mouse to the gaming PC.

## What AC sees

Nothing. Not ring-0 scans, not usermode signature scans, not kernel callback scans, not module list walks, not screenshot scans. The gaming PC has no cheat software running.

What remains:
- **Behavioral ML.** Perfect tracking still flags Vanguard's ML. Humanize — see `opsec/behavioral.md`.
- **PCIe DMA mitigation (future).** Intel VT-d / AMD IOMMU can restrict DMA. Vanguard does NOT currently require VT-d-enforcing-MMIO; if Riot flips that flag, the gaming PC refuses to boot with DMA enabled. Watch for it in release notes.

## Setup runbook

`docs/game-lab/setup-dma.md` — covers slot selection, firmware flashing, MemProcFS install, KMBox calibration, and the first per-game offset derivation.

## Reference build

`cheats/dma-stack/`:
- `pcileech_reader.py` — MemProcFS reader loop with entity cache
- `offset_finder.py` — AOB signature scanner over a game dump
- `README.md` — hardware checklist

## Lifespan

Invisible to every current software anticheat. Future risk is VT-d enforcement — if a game requires IOMMU enabled with DMA-remapping, this stack breaks. No consumer AC enforces it as of this writing. Monitor Vanguard release notes.
