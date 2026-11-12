"""Operator-mode tool adapters.

Each submodule exposes ``run(app, **kwargs) -> dict`` and parses its tool
output into a structured record. Keys the OperatorTools auto-ingestor
understands:

- ``hosts``:       ``[{ip, hostname?, project_id?, os_hint?}]``
- ``services``:    ``[{host_id, port, proto, name?, banner?}]``
- ``loot``:        ``[{kind, path, host_id?, service_id?, notes?}]``

Also every adapter returns ``{"tool", "command", "exit_code", "stdout",
"stderr", "duration_ms"}`` so the Timeline can replay it.
"""

from . import (
    nmap_adapter,
    httpx_adapter,
    ffuf_adapter,
    nuclei_adapter,
    hashcat_adapter,
    hydra_adapter,
    frida_adapter,
    radare2_adapter,
    ghidra_adapter,
    impacket_adapter,
    bloodhound_adapter,
    adb_adapter,
    subfinder_adapter,
)

__all__ = [
    "nmap_adapter", "httpx_adapter", "ffuf_adapter", "nuclei_adapter",
    "hashcat_adapter", "hydra_adapter", "frida_adapter", "radare2_adapter",
    "ghidra_adapter", "impacket_adapter", "bloodhound_adapter", "adb_adapter",
    "subfinder_adapter",
]
