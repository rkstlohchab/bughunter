# DMA stack reference

Hardware + software + offsets for a PCILeech-based DMA cheat. Adapter layer:
`backend/bughunter/op_adapters/dma_memread.py`.

## Hardware

- FPGA PCIe card: CaptainDMA Squirrel / LeetDMA / Enigma X1. Plugs into a
  free PCIe slot (x1 is fine for read-only) on the GAMING PC.
- USB-C or USB-A data cable to the READER PC.
- Optional: KMBox B+/B Pro for input splice; a real mouse goes through it,
  the gaming PC sees a standard mouse.

## Software — reader PC

```bash
# Install PCILeech + MemProcFS (reader-side only)
# Linux:
apt install build-essential libusb-1.0-0-dev libpcap-dev
git clone https://github.com/ufrisk/pcileech.git && cd pcileech && make && sudo cp pcileech /usr/local/bin/
git clone https://github.com/ufrisk/MemProcFS.git && cd MemProcFS && make

# Python bindings:
pip install memprocfs numpy

# Verify FPGA detected
pcileech probe

# Run the reader (CS2 example)
python3 pcileech_reader.py --process cs2.exe --offsets ../../data/gamelab_offsets/cs2.json
```

## Offsets

Live-dump + AOB-scan via `offset_finder.py`. The signature set lives in
`signatures/<game>.sig.json`. Patterns are IDA-style
(`48 8B ? ? C3`); unknown bytes `?` match any. `rip_relative` entries
resolve `lea rax, [rip+disp32]`-style references.

Pull the CS2 offsets fast via the a2x/cs2-dumper release:

```bash
# via the adapter (GameLab project only):
gamelab.offsets.refresh game=cs2 source=community
```

Which is a wrapper over:

```bash
curl -sL $(curl -sL https://api.github.com/repos/a2x/cs2-dumper/releases/latest \
  | jq -r '.assets[] | select(.name=="offsets.json") | .browser_download_url') \
  > ../../data/gamelab_offsets/cs2.json
```

## KMBox input

```python
from kmbox_api import KmBox   # or write JSON manually over TCP 65432
km = KmBox(ip="192.168.1.50", port=65432)
km.mouse_move(dx=42, dy=-13)
```

`pcileech_reader.py --aimbot --kmbox 192.168.1.50:65432` uses it automatically.

## Detection

Vanguard / BE / EAC / VAC: none can see PCIe-side reads. Behavioral ML on the
gaming PC still sees your aim curves — humanize (see `opsec/behavioral.md`).

Future risk: VT-d / IOMMU enforcement. If an AC starts requiring the OS
to enable DMA remapping with IOMMU, your FPGA reads become visible to the
IOMMU — mitigation is a BIOS-level IOMMU off setting, or an FPGA with
PCIe TLP-level stealth support (newer cards offer it).

## Per-game offset re-derivation

```bash
# Dump the game's image via DMA
cat /M/{pid}/dll/cs2.exe > /tmp/cs2.exe

# Scan for signatures
python3 offset_finder.py --image /tmp/cs2.exe --signatures signatures/cs2.sig.json --json \
  > ../../data/gamelab_offsets/cs2.json
```

Automate via `gamelab.offsets.refresh` with `source=scan`.
