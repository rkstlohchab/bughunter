# Setup — DMA (PCILeech + MemProcFS + KMBox)

Invisible to every current software anticheat. ~$230 all-in. Works
across Valorant, CS2, BE/EAC titles.

## Parts

- FPGA card. Any PCILeech-compatible:
  - CaptainDMA **Squirrel** (~$180) — most popular
  - **LeetDMA** (~$200)
  - **Enigma X1** (~$250)
- Reader PC — any modern laptop/desktop (reader is CPU-light)
- KMBox B+ or B Pro (~$50) — hardware mouse-input splicer
- Short PCIe x1 extension if your gaming PC's slot is awkward
- USB-C data cable: FPGA → reader PC

## Wiring

```
Gaming PC
 ├─ FPGA PCIe card ──USB-C──> Reader PC
 └─ USB keyboard/mouse ──> KMBox ──> Gaming PC
                              ^
                              └─LAN──> Reader PC
```

Reader PC and KMBox are on the same LAN.

## Step 1 — install the FPGA

Power off gaming PC, open case, plug the FPGA into any available PCIe
slot (x1 is fine — the card needs power + PCIe link; bandwidth is low).
Close, power on, enter UEFI. Verify the card enumerates:

```
UEFI → Advanced → PCI Configuration — should see "FTDI FT601" or
"Xilinx FPGA" as a new device.
```

## Step 2 — reader PC software

Linux (recommended):

```bash
sudo apt update
sudo apt install -y build-essential git pkg-config libusb-1.0-0-dev \
    libpcap-dev python3-pip

# PCILeech
git clone https://github.com/ufrisk/pcileech.git
cd pcileech/pcileech && make && sudo cp pcileech /usr/local/bin/
cd ../..

# MemProcFS
git clone https://github.com/ufrisk/MemProcFS.git
cd MemProcFS && make && sudo cp vmm.so /usr/local/lib/
sudo ldconfig
cd ..

# Python bindings
pip install memprocfs numpy
```

macOS / Windows: follow the PCILeech README — macOS requires driverless
libusb; Windows uses the PCILeech-supplied driver.

## Step 3 — verify FPGA detected

```bash
pcileech probe
# expected output includes "DEVICE: FPGA" or "USB3380" and a lane/width line
```

GameLab:
```
gamelab.start_recipe adapter=dma_memread config={mode:"probe"}
```

Returns `hardware_ok: true`.

## Step 4 — fingerprint the gaming PC

Boot the gaming PC. Launch the game. On the reader PC:

```bash
pcileech dump -min 0x1000000 -max 0x2000000 -out /tmp/sample.bin
# should produce a dump — if it fails with "no device", the FPGA isn't
# seeing host RAM. Re-seat, check PCIe link status in UEFI.
```

## Step 5 — pull offsets

CS2:
```
gamelab.start_recipe adapter=game_offsets config={
  mode:"refresh", game:"cs2", source:"community"
}
```

Downloads the latest `offsets.json` from a2x/cs2-dumper.

Valorant / other UE titles:
```
gamelab.start_recipe adapter=dma_memread config={
  mode:"offset_scan", game:"valorant",
  process_name:"VALORANT-Win64-Shipping.exe"
}
```

AOB-scans the live process via DMA. Results written to
`<data_dir>/gamelab_offsets/<game>.json`.

Signatures live under `cheats/dma-stack/signatures/<game>.sig.json`. For
a new game, write one — pattern-match the exports you want
(`dwEntityList`, `dwLocalPlayer`, `dwViewMatrix`, etc.).

## Step 6 — KMBox setup

Connect KMBox:
- Mouse plugs into KMBox's USB-in port
- KMBox's USB-out plugs into the gaming PC
- KMBox's Ethernet jack goes to the LAN

Configure KMBox via its web admin (http://kmbox.local or its DHCP IP).
Note the IP — pass to the reader as `kmbox_endpoint`.

Verify:
```
gamelab.start_recipe adapter=kmbox config={
  mode:"probe", endpoint:"192.168.1.50:65432"
}
```

Should return `ok:true`.

## Step 7 — start the reader

ESP only:
```
gamelab.start_recipe project_id=<id> adapter=dma_memread config={
  mode:"start",
  game:"cs2",
  process_name:"cs2.exe",
  offsets_path:"<data_dir>/gamelab_offsets/cs2.json",
  humanize:true
}
```

The reader loop runs at ~200Hz. Write-tee of the per-frame snapshot
lands in `/tmp/gamelab_dma_frame.json`. For overlay, run the overlay
renderer:

```bash
python3 cheats/dma-stack/esp_overlay.py --live   # TODO — second-screen overlay
```

(Overlay renderer intentionally external — pair with an HDMI capture
of the gaming PC's monitor for a second-screen ESP, same as the vision
stack, but with full team/HP data from DMA.)

ESP + aim via KMBox:
```
gamelab.start_recipe project_id=<id> adapter=dma_memread config={
  mode:"start",
  game:"cs2",
  process_name:"cs2.exe",
  offsets_path:"<data_dir>/gamelab_offsets/cs2.json",
  kmbox_endpoint:"192.168.1.50:65432",
  humanize:true
}
```

Reader walks pointers every ~5ms; aim corrections tick at 90ms with
humanization.

## Step 8 — per-patch workflow

After each game update:

```
gamelab.start_recipe adapter=game_offsets config={
  mode:"refresh", game:"cs2", source:"community"
}
```

CS2 patches Tuesday → cs2-dumper commits within hours → one call and
you're caught up.

For Valorant / UE titles:
```
# Dump current image via DMA:
# (shell on reader) cat /M/<pid>/dll/valorant-win64-shipping.exe > /tmp/val.exe
gamelab.start_recipe adapter=game_offsets config={
  mode:"scan", game:"valorant", image_path:"/tmp/val.exe"
}
```

## Opsec

DMA defeats every current software AC. Behavioral ML is still live:

- Reaction floor: 180ms
- Miss rate: 0.08
- HS% cap at your rank's 95th percentile
- Spray transfer at 68% not 100%

`gamelab.opsec_check` before each session.

## Troubleshooting

- **`pcileech probe` sees no FPGA** — USB cable (data not power). Re-seat
  PCIe. Check USB 3.x port used (must be 3.0+).
- **MemProcFS returns 0x00... for every read** — gaming PC is in modern
  standby/connected-standby, RAM is compressed. Disable connected-standby
  in Windows (registry).
- **KMBox doesn't move cursor** — endpoint reachable? `nc -zv kmbox-ip 65432`
  succeeds? Check KMBox has firmware ≥ 1.3; older firmware uses UDP not TCP.
- **"VMX enforcement" from AC** — ignore; DMA runs even with VBS on.

## When DMA breaks

Future risk: if Microsoft + anticheat vendors require IOMMU with
DMA-remapping ON for the game to launch, bare FPGA reads become
IOMMU-visible. Mitigations:
- BIOS → IOMMU → disabled (game refuses to launch → not useful)
- FPGA firmware with TLP-level stealth support (newer cards offer this)

As of this writing no retail game enforces this. Monitor Vanguard
release notes; Microsoft driver-model changes signal direction.
