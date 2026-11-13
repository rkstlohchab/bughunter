"""GameLab — operator-mode game cheat workbench.

Parallel to WebsiteAudit / CommerceAudit / InvestigationP10, lives entirely
in the operator stack. The investigation ModelTools surface is untouched.

RPC namespace: ``gamelab.*``

The AI guide runs as an OperatorAgent with the ``prompts/game-cheat-ops.md``
runtime prompt. Knowledge retrieval uses the existing KnowledgeService
backed by FTS5 + vectors. Entity kinds added by this service:

- ``cheat_project``    — a GameLab project (target game + hardware recipe)
- ``cheat_recipe``     — one running pipeline (vision_esp, dma_memread, …)
- ``hardware_asset``   — detected hardware (capture card, FPGA, Arduino, phone)
- ``offset_set``       — per-build offset table for a game
- ``opsec_profile``    — humanization + pacing settings for a target account

All memory- / hardware-touching work happens in the ``op_adapters``. This
service is the orchestrator + state store.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .store import new_id, now
from .operator_tools import OperatorTools


GAME_PROFILES = {
    "valorant": {
        "label": "Valorant",
        "anticheat": "vanguard",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga", "external-vision", "hypervisor"],
        "stacks_blocked": ["internal", "transparent-overlay"],
        "notes": "Kernel boot-start AC. DMA + KMBox is the stable path.",
    },
    "cs2": {
        "label": "Counter-Strike 2",
        "anticheat": "vac-trust",
        "platform": "windows",
        "stacks_recommended": ["external-vision", "dma-fpga", "kernel-mapper"],
        "stacks_blocked": [],
        "notes": "Trust Factor is the silent killer. Behavioral opsec > tech.",
    },
    "bgmi": {
        "label": "BGMI / PUBG Mobile (Android)",
        "anticheat": "bgmi-ace",
        "platform": "android",
        "stacks_recommended": ["external-vision", "mobile-root"],
        "stacks_blocked": ["internal"],
        "notes": "scrcpy + PC YOLO is the invisible path. On rooted phone, Zygisk memory ESP.",
    },
    "bgmi_ios": {
        "label": "BGMI / PUBGM (iOS, non-jailbroken)",
        "anticheat": "bgmi-ace",
        "platform": "ios",
        "stacks_recommended": ["ios-sideload"],
        "stacks_blocked": ["internal", "external-vision"],
        "notes": "Patched-IPA + dylib + AltStore sign. Memory ESP + in-process touch aim.",
    },
    "fortnite": {
        "label": "Fortnite",
        "anticheat": "eac",
        "platform": "windows",
        "stacks_recommended": ["external-vision", "dma-fpga", "kernel-mapper"],
        "stacks_blocked": [],
        "notes": "BE/EAC hybrid; moderate aggression. Behavioral opsec + external.",
    },
    "apex": {
        "label": "Apex Legends",
        "anticheat": "eac",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga", "external-vision", "kernel-mapper"],
        "stacks_blocked": [],
        "notes": "Historically porous; still ban-wave heavy. Private kernel OK.",
    },
    "rust": {
        "label": "Rust",
        "anticheat": "eac",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga", "kernel-mapper"],
        "stacks_blocked": ["internal"],
        "notes": "Aggressive HWID + payment ban. HWID spoof essential.",
    },
    "r6_siege": {
        "label": "Rainbow Six Siege",
        "anticheat": "battleye",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga", "kernel-mapper", "external-vision"],
        "stacks_blocked": [],
        "notes": "BE signature waves are the fast-ban; private mapper fine.",
    },
    "tarkov": {
        "label": "Escape from Tarkov",
        "anticheat": "battleye",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga"],
        "stacks_blocked": ["external-vision", "internal"],
        "notes": "Extremely aggressive BE. DMA + behavioral opsec only.",
    },
    "cod_mw": {
        "label": "Call of Duty: Modern Warfare / Warzone",
        "anticheat": "ricochet",
        "platform": "windows",
        "stacks_recommended": ["dma-fpga", "hypervisor"],
        "stacks_blocked": ["internal", "kernel-mapper-public"],
        "notes": "Ricochet (kernel) + behavioral ML. DMA + HV for lifespan.",
    },
    "cod_mobile": {
        "label": "Call of Duty: Mobile",
        "anticheat": "ace-tencent",
        "platform": "android",
        "stacks_recommended": ["external-vision", "mobile-root"],
        "stacks_blocked": ["internal"],
        "notes": "Same stack as BGMI. scrcpy + PC YOLO.",
    },
    "free_fire": {
        "label": "Free Fire",
        "anticheat": "garena-shield",
        "platform": "android",
        "stacks_recommended": ["external-vision", "mobile-root"],
        "stacks_blocked": ["internal"],
        "notes": "Aggressive IMEI + region ban. Rooted + MagiskHide required.",
    },
    "fivem": {
        "label": "FiveM (GTA V)",
        "anticheat": "fivem-shield",
        "platform": "windows",
        "stacks_recommended": ["kernel-mapper", "external-vision"],
        "stacks_blocked": [],
        "notes": "Per-server AC configs; much easier than retail GTA.",
    },
    "dayz": {
        "label": "DayZ",
        "anticheat": "battleye",
        "platform": "windows",
        "stacks_recommended": ["kernel-mapper", "dma-fpga"],
        "stacks_blocked": [],
        "notes": "BE moderate aggression; per-server rules vary.",
    },
    "among_us": {
        "label": "Among Us (test target — no AC on mobile)",
        "anticheat": "eac-light",
        "platform": "cross",
        "stacks_recommended": ["ios-sideload", "mobile-root", "external-vision", "dma-fpga"],
        "stacks_blocked": [],
        "notes": "Perfect first-run cheat target. Memory writes work on offline Freeplay unconditionally; online matchmade games have host-authoritative cooldown but client wallhack still works.",
    },
}

FEATURE_MATRIX = {
    "cs2":       {"engine": "source2",   "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": False, "has_offsets_dumper": True},
    "valorant":  {"engine": "unreal",    "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": False, "has_offsets_dumper": False},
    "apex":      {"engine": "apex",      "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": True,
                  "loot": True, "has_offsets_dumper": False},
    "rust":      {"engine": "unity",     "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": True,
                  "loot": True, "has_offsets_dumper": False},
    "fortnite":  {"engine": "unreal",    "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "r6_siege":  {"engine": "anvil",     "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": False, "has_offsets_dumper": False},
    "tarkov":    {"engine": "unity",     "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "cod_mw":    {"engine": "iw",        "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": False, "has_offsets_dumper": False},
    "pubg":      {"engine": "unreal",    "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "bgmi":      {"engine": "unreal_mobile", "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "bgmi_ios":  {"engine": "unreal_ios",    "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "among_us":  {"engine": "unity_cross",   "esp": True, "radar": False,
                  "aim": False, "trigger": False, "recoil": False,
                  "loot": False, "has_offsets_dumper": True,
                  "extras": ["no_cooldown", "fog_off", "role_reveal"]},
    "cod_mobile":{"engine": "unreal_mobile", "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": False, "has_offsets_dumper": False},
    "free_fire": {"engine": "unity_mobile", "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
    "fivem":     {"engine": "rage",      "esp": True, "radar": True,
                  "aim": True, "trigger": True, "recoil": True,
                  "loot": False, "has_offsets_dumper": False},
    "dayz":      {"engine": "enfusion",  "esp": True, "radar": True,
                  "aim": True, "trigger": False, "recoil": False,
                  "loot": True, "has_offsets_dumper": False},
}


STACKS = {
    "external-vision": {
        "label": "External vision (capture card + YOLO + Arduino HID)",
        "lifespan_class": "invisible",
        "cost_floor_usd": 80,
        "hardware": ["hdmi_splitter", "capture_card", "second_pc_gpu", "arduino_leonardo"],
        "docs": "stacks/external-vision.md",
        "setup_doc": "docs/game-lab/setup-external-vision.md",
    },
    "dma-fpga": {
        "label": "DMA FPGA (PCILeech + MemProcFS + KMBox)",
        "lifespan_class": "invisible",
        "cost_floor_usd": 230,
        "hardware": ["fpga_dma_card", "second_pc", "kmbox_net"],
        "docs": "stacks/dma-fpga.md",
        "setup_doc": "docs/game-lab/setup-dma.md",
    },
    "kernel-mapper": {
        "label": "Kernel manual-mapper (BYOVD)",
        "lifespan_class": "weeks",
        "cost_floor_usd": 0,
        "hardware": [],
        "docs": "stacks/kernel-mapper.md",
        "setup_doc": "docs/game-lab/setup-kernel-mapper.md",
    },
    "hypervisor": {
        "label": "Type-1 hypervisor",
        "lifespan_class": "months",
        "cost_floor_usd": 0,
        "hardware": ["uefi_board", "flashback_support"],
        "docs": "stacks/hypervisor.md",
        "setup_doc": "docs/game-lab/setup-hypervisor.md",
    },
    "mobile-root": {
        "label": "Rooted Android + Magisk + Zygisk",
        "lifespan_class": "weeks",
        "cost_floor_usd": 0,
        "hardware": ["unlockable_phone"],
        "docs": "stacks/mobile-root.md",
        "setup_doc": "docs/game-lab/setup-bgmi.md",
    },
    "ios-sideload": {
        "label": "iOS non-jailbroken — patched IPA + AltStore (memory ESP + in-proc aim)",
        "lifespan_class": "days",
        "cost_floor_usd": 0,
        "hardware": ["iphone", "mac"],
        "docs": "ios_memory/README.md",
        "setup_doc": "docs/game-lab/setup-ios-bgmi.md",
    },
}


@dataclass
class HardwareInventory:
    """Snapshot of hardware available on this machine or reachable from it."""
    host_platform: str = ""
    cpu: str = ""
    gpu_vendor: str = ""
    gpu_name: str = ""
    cuda_available: bool = False
    mlx_available: bool = False
    capture_cards: list[str] = field(default_factory=list)
    serial_ports: list[dict] = field(default_factory=list)
    arduino_candidates: list[dict] = field(default_factory=list)
    adb_devices: list[dict] = field(default_factory=list)
    pcileech_device: dict | None = None
    kmbox_net_endpoint: str | None = None
    yolo_installed: bool = False
    scrcpy_installed: bool = False
    opencv_installed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class GameLabService:
    """Orchestrator for game-cheat projects. Runs under operator mode.

    Nothing here imports ``ModelTools``, ``PolicyService``, or
    ``RunManager``. Cross-plumbing with the investigation path is
    forbidden by AGENTS.md §10.4 invariant 3.
    """

    def __init__(self, app):
        self.app = app
        self.store = app.store

    # -------- RPC dispatch -------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        if method=='gamelab.setup':
            from .gamelab_setup import inspect_setup
            return inspect_setup(self.app)
        p = params or {}
        if method == "gamelab.list_games":
            return {"games": [{"id": k, **v,
                                "features": FEATURE_MATRIX.get(k, {})}
                               for k, v in GAME_PROFILES.items()]}
        if method == "gamelab.list_stacks":
            return {"stacks": [{"id": k, **v} for k, v in STACKS.items()]}
        if method == "gamelab.feature_matrix":
            return {"features": FEATURE_MATRIX}
        if method == "gamelab.start_full_kit":
            return self.start_full_kit(
                project_id=p["project_id"],
                features=p.get("features", {}),
                hid=p.get("hid", {}),
            )
        if method == "gamelab.chat_start":
            return self.chat_start(project_id=p.get("project_id"),
                                    message=p["message"])
        if method == "gamelab.chat_send":
            return self.chat_send(run_id=p["run_id"], message=p["message"])
        if method == "gamelab.chat_get":
            return self.chat_get(run_id=p["run_id"])
        if method == "gamelab.chat_list":
            return {"runs": self.chat_list(project_id=p.get("project_id"))}
        if method == "gamelab.chat_stop":
            return self.chat_stop(run_id=p["run_id"])
        if method == "gamelab.hardware_check":
            return self.hardware_check(force=bool(p.get("force", False)))
        if method == "gamelab.create_project":
            return self.create_project(
                name=p["name"],
                game=p["game"],
                stack=p.get("stack"),
                notes=p.get("notes", ""),
            )
        if method == "gamelab.list_projects":
            return {"projects": self._list_projects()}
        if method == "gamelab.get_project":
            return self._get_project(p["id"])
        if method == "gamelab.recommend_stack":
            return self.recommend_stack(game=p["game"],
                                        inventory=p.get("inventory"))
        if method == "gamelab.start_recipe":
            return self.start_recipe(project_id=p["project_id"],
                                     adapter=p["adapter"],
                                     config=p.get("config", {}))
        if method == "gamelab.stop_recipe":
            return self.stop_recipe(recipe_id=p["recipe_id"])
        if method == "gamelab.list_recipes":
            return {"recipes": self._list_recipes(p.get("project_id"))}
        if method == "gamelab.offsets.get":
            return self._get_offsets(game=p["game"])
        if method == "gamelab.offsets.refresh":
            return self.refresh_offsets(game=p["game"],
                                        source=p.get("source", "auto"))
        if method == "gamelab.opsec_check":
            return self.opsec_check(project_id=p["project_id"],
                                    stats=p.get("stats", {}))
        if method == "gamelab.knowledge_search":
            return self.knowledge_search(query=p["query"],
                                         top_k=int(p.get("top_k", 6)))
        if method == "gamelab.capture_dataset":
            return self.capture_dataset(project_id=p["project_id"],
                                        source=p["source"],
                                        duration_s=int(p.get("duration_s", 300)),
                                        fps=int(p.get("fps", 5)))
        if method == "gamelab.ask":
            return self.ask_guide(project_id=p.get("project_id"),
                                  message=p["message"])
        raise ValueError(f"unknown gamelab method: {method}")

    # -------- hardware ----------------------------------------------------

    def hardware_check(self, force: bool = False) -> dict:
        """Probe for hardware relevant to GameLab stacks. Cached per session."""
        cached = self.store.setting("gamelab_hw_inventory", {})
        if cached and not force and time.time() - cached.get("_stamp", 0) < 600:
            return cached
        inv = HardwareInventory(host_platform=platform.system())
        inv.cpu = _probe_cpu()
        gpu = _probe_gpu()
        inv.gpu_vendor, inv.gpu_name = gpu
        inv.cuda_available = _probe_cuda()
        inv.mlx_available = _probe_mlx()
        inv.capture_cards = _probe_capture_cards()
        ports, arduinos = _probe_serial_ports()
        inv.serial_ports = ports
        inv.arduino_candidates = arduinos
        inv.adb_devices = _probe_adb()
        inv.pcileech_device = _probe_pcileech()
        inv.yolo_installed = _probe_python_mod("ultralytics")
        inv.scrcpy_installed = shutil.which("scrcpy") is not None
        inv.opencv_installed = _probe_python_mod("cv2")
        snapshot = inv.to_dict()
        snapshot["_stamp"] = time.time()
        self.store.set_setting("gamelab_hw_inventory", snapshot)
        self._emit_timeline("hardware_check", payload={"found": {
            "capture_cards": len(inv.capture_cards),
            "arduinos": len(inv.arduino_candidates),
            "adb": len(inv.adb_devices),
            "pcileech": bool(inv.pcileech_device),
        }})
        return snapshot

    # -------- recommendations --------------------------------------------

    def recommend_stack(self, game: str, inventory: dict | None = None) -> dict:
        profile = GAME_PROFILES.get(game)
        if not profile:
            raise ValueError(f"unknown game: {game}")
        inv = inventory or self.hardware_check()
        picks: list[dict] = []
        for stack_id in profile["stacks_recommended"]:
            stack = STACKS[stack_id]
            missing = [h for h in stack["hardware"] if not _inventory_satisfies(inv, h)]
            picks.append({
                "stack": stack_id,
                "label": stack["label"],
                "lifespan_class": stack["lifespan_class"],
                "cost_floor_usd": stack["cost_floor_usd"],
                "ready": not missing,
                "missing_hardware": missing,
                "docs": stack["docs"],
                "setup_doc": stack["setup_doc"],
            })
        return {"game": game, "anticheat": profile["anticheat"],
                "notes": profile["notes"], "picks": picks}

    # -------- projects ---------------------------------------------------

    def create_project(self, name: str, game: str,
                        stack: str | None = None, notes: str = "") -> dict:
        if game not in GAME_PROFILES:
            raise ValueError(f"unknown game: {game}")
        if stack is not None and stack not in STACKS:
            raise ValueError(f"unknown stack: {stack}")
        pid = new_id("cproj")
        rec = {
            "id": pid, "name": name, "game": game, "stack": stack,
            "notes": notes, "status": "active", "created_at": now(),
            "recipes": [],
        }
        self.store.put("cheat_project", rec, id=pid)
        self._emit_timeline("create_project", payload={"id": pid, "game": game,
                                                       "stack": stack})
        return rec

    def _list_projects(self) -> list[dict]:
        return list(self.store.list("cheat_project"))

    def _get_project(self, pid: str) -> dict:
        rec = self.store.get("cheat_project", pid)
        if not rec:
            raise ValueError(f"no such project: {pid}")
        rec["recipes"] = self._list_recipes(pid)
        rec["opsec"] = self._opsec_profile(pid)
        return rec

    # -------- recipes ----------------------------------------------------

    def start_recipe(self, project_id: str, adapter: str,
                      config: dict) -> dict:
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False):raise ValueError('Tasks are stopped. Use Resume tasks first.')
        proj = self.store.get("cheat_project", project_id)
        if not proj:
            raise ValueError(f"no such project: {project_id}")
        rid = new_id("crcp")
        rec = {
            "id": rid, "project_id": project_id, "adapter": adapter,
            "config": config, "status": "starting",
            "created_at": now(), "updated_at": now(), "pid": None,
            "log_tail": [],
        }
        self.store.put("cheat_recipe", rec, id=rid)
        # Delegates execution to OperatorTools.invoke_adapter, which
        # the operator has already approved. GameLab hands over the
        # adapter name + args; OperatorTools handles timeline + asset
        # ingest.
        try:
            tools = OperatorTools(self.app, run_id=rid)
            result = tools.invoke_adapter(name=adapter, **config,
                                          project_id=project_id)
            rec["status"] = "running"
            rec["last_result"] = _tail(result)
            rec["pid"] = result.get("pid")
        except Exception as exc:
            rec["status"] = "error"
            rec["error"] = str(exc)
        rec["updated_at"] = now()
        self.store.put("cheat_recipe", rec, id=rid)
        self._emit_timeline("recipe_start", payload={"id": rid,
                                                     "adapter": adapter,
                                                     "project_id": project_id})
        return rec

    def stop_recipe(self, recipe_id: str) -> dict:
        rec = self.store.get("cheat_recipe", recipe_id)
        if not rec:
            raise ValueError(f"no such recipe: {recipe_id}")
        # Adapters that spawn a long-running process register a stop RPC;
        # we delegate via the adapter's "stop" verb if present.
        adapter = rec.get("adapter")
        tools = OperatorTools(self.app, run_id=recipe_id)
        try:
            # Cleanup remains available after the global execution latch closes.
            cleanup=tools._adapter_registry.get(adapter)
            if not cleanup:raise ValueError('No cleanup adapter exists.')
            cleanup(self.app,mode='stop',pid=rec.get('pid'))
            rec["status"] = "stopped"
        except Exception as exc:
            rec["status"] = "error"
            rec["error"] = str(exc)
        rec["updated_at"] = now()
        self.store.put("cheat_recipe", rec, id=recipe_id)
        self._emit_timeline("recipe_stop", payload={"id": recipe_id})
        return rec

    def _list_recipes(self, project_id: str | None = None) -> list[dict]:
        rows = self.store.list("cheat_recipe")
        if project_id:
            return [r for r in rows if r.get("project_id") == project_id]
        return list(rows)

    # -------- offsets ----------------------------------------------------

    def _offset_path(self, game: str) -> Path:
        return Path(self.store.root) / "gamelab_offsets" / f"{game}.json"

    def _get_offsets(self, game: str) -> dict:
        p = self._offset_path(game)
        if not p.exists():
            return {"game": game, "offsets": {}, "updated_at": None}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"game": game, "offsets": {}, "updated_at": None,
                    "error": "parse"}

    def refresh_offsets(self, game: str, source: str = "auto") -> dict:
        """Pull latest offsets for a game. For now: calls an op_adapter
        that wraps game-specific dumpers (cs2-dumper, hazedumper, custom
        AOB scans against a memory image)."""
        p = self._offset_path(game)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tools = OperatorTools(self.app, run_id=None)
        try:
            result = tools.invoke_adapter(name="game_offsets", mode="refresh",
                                          game=game, source=source)
            if result.get("offsets"):
                data = {
                    "game": game, "offsets": result["offsets"],
                    "updated_at": now(), "source": source,
                    "notes": result.get("notes", ""),
                }
                p.write_text(json.dumps(data, indent=2), encoding="utf-8")
                self._emit_timeline("offsets_refresh",
                                    payload={"game": game, "ok": True})
                return data
        except Exception as exc:
            self._emit_timeline("offsets_refresh",
                                payload={"game": game, "ok": False,
                                         "err": str(exc)})
            return {"game": game, "error": str(exc)}
        return {"game": game, "error": "no offsets returned"}

    # -------- opsec ------------------------------------------------------

    def _opsec_profile(self, project_id: str) -> dict:
        rows = list(self.store.list("opsec_profile"))
        for r in rows:
            if r.get("project_id") == project_id:
                return r
        default = {
            "id": new_id("ops"), "project_id": project_id,
            "reaction_floor_ms": 180, "aim_smooth": 0.35,
            "miss_rate": 0.08, "tick_ms": 90, "deadzone_px": 3,
            "pre_fire_frames": 4, "hs_percent_target": 32,
            "spray_correction": 0.68, "humanize_enabled": True,
            "created_at": now(),
        }
        self.store.put("opsec_profile", default, id=default["id"])
        return default

    def opsec_check(self, project_id: str, stats: dict) -> dict:
        """Score a stats dump (kd, hs%, winrate, climb rate) against floors."""
        prof = self._opsec_profile(project_id)
        flags: list[str] = []
        if stats.get("hs_percent", 0) > 55:
            flags.append(f"HS% {stats['hs_percent']} > 55 cap")
        if stats.get("winrate", 0) > 68:
            flags.append(f"winrate {stats['winrate']} > 68%")
        if stats.get("pre_fire_ratio", 0) > 0.18:
            flags.append(f"pre-fire {stats['pre_fire_ratio']} > 0.18")
        if stats.get("climb_per_week", 0) > 2:
            flags.append(f"climb {stats['climb_per_week']} ranks/week too fast")
        if stats.get("adr", 0) > 150:
            flags.append(f"ADR {stats['adr']} > 150 (CS2 cap)")
        reaction = stats.get("avg_reaction_ms")
        if reaction is not None and reaction < prof["reaction_floor_ms"]:
            flags.append(f"reaction avg {reaction}ms below floor")
        return {"profile": prof, "stats": stats, "flags": flags,
                "ok": not flags}

    # -------- capture + training -----------------------------------------

    def capture_dataset(self, project_id: str, source: str,
                         duration_s: int, fps: int) -> dict:
        """Delegates to vision_esp adapter in dataset-capture mode."""
        tools = OperatorTools(self.app, run_id=None)
        return tools.invoke_adapter(name="vision_esp", mode="capture_dataset",
                                    project_id=project_id, source=source,
                                    duration_s=duration_s, fps=fps)

    # -------- knowledge RAG ----------------------------------------------

    def knowledge_search(self, query: str, top_k: int = 6) -> dict:
        """Search the game-knowledge corpus. Uses KnowledgeService FTS5
        first (post-install), and falls back to a raw markdown grep on
        the shipped corpus so results are available even before indexing."""
        corpus_root = Path(__file__).resolve().parents[2] / "prompts" / "game-knowledge"
        hits: list[dict] = []
        try:
            fts_results = self.app.knowledge.search(query=query, limit=top_k)
            # Filter to our corpus by matching source filename against our tree
            corpus_names = {p.name for p in corpus_root.rglob("*.md")}
            for r in fts_results:
                src = str(r.get("source") or r.get("filename") or r.get("title") or "")
                if any(name in src for name in corpus_names):
                    hits.append(r)
            if hits:
                return {"query": query, "results": hits[:top_k], "source": "fts5"}
        except Exception:
            pass
        # Fallback: grep scan
        if corpus_root.exists():
            q = query.lower()
            for md in corpus_root.rglob("*.md"):
                text = md.read_text(encoding="utf-8", errors="ignore")
                if q in text.lower():
                    idx = text.lower().find(q)
                    start = max(0, idx - 200)
                    end = min(len(text), idx + 400)
                    hits.append({
                        "path": str(md.relative_to(corpus_root.parent.parent)),
                        "snippet": text[start:end],
                    })
                    if len(hits) >= top_k:
                        break
        return {"query": query, "results": hits, "source": "grep-fallback"}

    # -------- AI guide ---------------------------------------------------

    # -------- start_full_kit -----------------------------------------------

    def start_full_kit(self, project_id: str, features: dict,
                        hid: dict) -> dict:
        """Start every enabled feature for a project in one call.

        features = { "memory_esp": bool, "radar": bool, "aim": bool,
                     "trigger": bool, "recoil": bool, "chams_mode": str,
                     "draw_box"/"draw_snapline"/etc.: bool }
        hid      = { "backend": "arduino"|"kmbox"|"adb",
                     "arduino_port": str, "kmbox_endpoint": str,
                     "adb_serial": str }
        """
        proj = self.store.get("cheat_project", project_id)
        if not proj:
            raise ValueError(f"no such project: {project_id}")
        game = proj["game"]
        feat = FEATURE_MATRIX.get(game, {})
        if not feat.get("esp"):
            raise ValueError(f"game {game} has no supported engine walker yet")
        engine = feat.get("engine", "")
        is_mobile_android = engine == "unreal_mobile" or engine == "unity_mobile"
        is_mobile_ios = engine == "unreal_ios"
        results: dict = {"project_id": project_id, "game": game,
                         "steps": []}

        # Pipeline 1: memory_esp (always on if asked). Route by platform:
        # Android → mobile_memory_esp (adb forward + Zygisk)
        # iOS     → ios_memory_esp     (iproxy forward + patched-IPA dylib)
        # PC      → memory_esp         (DMA / RPM)
        if features.get("memory_esp", True):
            if is_mobile_ios:
                adapter = "ios_memory_esp"
                adapter_cfg = {
                    "mode": "start",
                    "game": game,
                    "project_id": project_id,
                    "udid": hid.get("ios_udid"),
                    "port": int(hid.get("ios_port", 65510)),
                    "chams_mode": features.get("chams_mode", "team"),
                    "features": {k: v for k, v in features.items()
                                  if k.startswith("draw_")},
                }
            elif is_mobile_android:
                adapter = "mobile_memory_esp"
                adapter_cfg = {
                    "mode": "start",
                    "game": game,
                    "project_id": project_id,
                    "serial": hid.get("adb_serial"),
                    "chams_mode": features.get("chams_mode", "team"),
                    "features": {k: v for k, v in features.items()
                                  if k.startswith("draw_")},
                }
            else:
                adapter = "memory_esp"
                adapter_cfg = {
                    "mode": "start",
                    "game": game,
                    "project_id": project_id,
                    "reader_backend": _pick_reader_backend(game),
                    "chams_mode": features.get("chams_mode", "team"),
                    "features": {k: v for k, v in features.items()
                                  if k.startswith("draw_")},
                }
            recipe = self.start_recipe(project_id=project_id,
                                        adapter=adapter,
                                        config=adapter_cfg)
            results["steps"].append({"step": "memory_esp", "recipe": recipe})
            shared_file = (recipe.get("last_result") or {}).get("shared_file")
        else:
            shared_file = None

        # Pipeline 2: radar
        if features.get("radar", True):
            r_rec = self.start_recipe(project_id=project_id, adapter="radar",
                                       config={"mode": "start",
                                               "shared_file": shared_file,
                                               "range_m": features.get("radar_range_m", 300.0)})
            results["steps"].append({"step": "radar", "recipe": r_rec})

        # Pipeline 3: combat aim
        if features.get("aim", False) and feat.get("aim", True):
            c_rec = self.start_recipe(project_id=project_id, adapter="combat",
                                       config={
                                           "mode": "start",
                                           "feature": "aim",
                                           "shared_file": shared_file,
                                           "hid_backend": hid.get("backend", "arduino"),
                                           "arduino_port": hid.get("arduino_port"),
                                           "kmbox_endpoint": hid.get("kmbox_endpoint"),
                                           "adb_serial": hid.get("adb_serial"),
                                           "trigger_enabled": features.get("trigger", False),
                                           "aim_smooth": features.get("aim_smooth", 0.42),
                                           "miss_rate": features.get("miss_rate", 0.08),
                                           "reaction_floor_ms": features.get("reaction_floor_ms", 180),
                                           "fov_px": features.get("fov_px", 180.0),
                                       })
            results["steps"].append({"step": "combat_aim", "recipe": c_rec})

        # Pipeline 4: recoil writer
        if features.get("recoil", False) and feat.get("recoil", False):
            r_rec = self.start_recipe(project_id=project_id, adapter="combat",
                                       config={
                                           "mode": "start",
                                           "feature": "recoil",
                                           "game": game,
                                           "process_name": _default_process_name(game),
                                           "reader_backend": _pick_reader_backend(game),
                                       })
            results["steps"].append({"step": "combat_recoil", "recipe": r_rec})

        self._emit_timeline("start_full_kit",
                            payload={"project_id": project_id, "game": game,
                                     "step_count": len(results["steps"])})
        return results

    def ask_guide(self, project_id: str | None, message: str) -> dict:
        """One-shot alias of chat_start — kept for the old UI button."""
        return self.chat_start(project_id=project_id, message=message)

    # -------- multi-turn chat with the guide ------------------------------

    def chat_start(self, project_id: str | None, message: str) -> dict:
        """Start a multi-turn thread with the GameLab guide. Loads the
        game-cheat-ops runtime prompt + current project/hardware context,
        then kicks off an OperatorAgent run. Use ``chat_send`` to continue
        on the same run_id."""
        runtime_prompt = _load_runtime_prompt()
        ctx_lines = ["# GameLab session context", ""]
        if project_id:
            proj = self._get_project(project_id)
            ctx_lines.append(f"## Active project\n```json\n{json.dumps(proj, indent=2, default=str)}\n```")
        hw = self.hardware_check()
        ctx_lines.append(f"## Hardware inventory\n```json\n{json.dumps(hw, indent=2, default=str)}\n```")
        ctx_lines.append("")
        ctx_lines.append("# Operator message")
        ctx_lines.append(message)
        full_prompt = runtime_prompt + "\n\n---\n\n" + "\n".join(ctx_lines)
        run = self.app.operator.start(prompt=message,context=full_prompt.split('# Operator message')[0],
                                       objective=f"gamelab:{project_id or 'adhoc'}",
                                       approved=True)
        self._emit_timeline("chat_start",
                            payload={"run_id": run.get("id"),
                                     "project_id": project_id})
        return {"run_id": run.get("id"), "status": run.get("status"),
                "project_id": project_id}

    def chat_send(self, run_id: str, message: str) -> dict:
        """Continue an existing gamelab chat thread."""
        rec = self.app.operator.send(id=run_id, message=message)
        return {"run_id": run_id, "status": rec.get("status")}

    def chat_get(self, run_id: str) -> dict:
        """Fetch the operator-run record (transcript + status)."""
        rec = self.app.operator.get(id=run_id)
        if not rec:
            return {"run_id": run_id, "status": "missing", "transcript": []}
        return {
            "run_id": run_id,
            "status": rec.get("status"),
            "turns_done": rec.get("turns_done", 0),
            "transcript": [m for m in rec.get('transcript',[]) if m.get('role') in {'user','assistant'} and m.get('content') and not m.get('tool_calls')],
            "objective": rec.get("objective", ""),
            "created_at": rec.get("created_at"),
        }

    def chat_list(self, project_id: str | None) -> list[dict]:
        """List gamelab chats (filtered by project if asked)."""
        runs = self.app.operator.list()
        prefix = f"gamelab:{project_id}" if project_id else "gamelab:"
        return [
            {"id": r.get("id"), "objective": r.get("objective", ""),
             "status": r.get("status"), "created_at": r.get("created_at"),
             "turns_done": r.get("turns_done", 0)}
            for r in runs
            if str(r.get("objective", "")).startswith(prefix)
        ]

    def chat_stop(self, run_id: str) -> dict:
        rec = self.app.operator.stop(id=run_id)
        return {"run_id": run_id, "status": rec.get("status")}

    # -------- internals --------------------------------------------------

    def _emit_timeline(self, phase: str, payload: dict | None = None) -> None:
        tl = getattr(self.app, "timeline", None)
        if tl:
            tl.emit(kind="gamelab", phase=phase, payload=payload or {})


# ===========================================================================
# Hardware probes
# ===========================================================================


def _run_quiet(argv: list[str], timeout: int = 5) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.stdout or ""
    except Exception:
        return ""


def _probe_cpu() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        out = _run_quiet(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out.strip()
    if sysname == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or ""


def _probe_gpu() -> tuple[str, str]:
    sysname = platform.system()
    if sysname == "Darwin":
        out = _run_quiet(["system_profiler", "SPDisplaysDataType"])
        for line in out.splitlines():
            if "Chipset Model" in line:
                return ("Apple", line.split(":", 1)[1].strip())
        return ("Apple", "unknown")
    if sysname == "Linux":
        out = _run_quiet(["lspci", "-mm"])
        for line in out.splitlines():
            low = line.lower()
            if "vga" in low or "3d controller" in low:
                if "nvidia" in low:
                    return ("nvidia", line)
                if "amd" in low or "radeon" in low:
                    return ("amd", line)
                if "intel" in low:
                    return ("intel", line)
    return ("unknown", "")


def _probe_cuda() -> bool:
    if _run_quiet(["nvidia-smi", "-L"]).strip():
        return True
    return False


def _probe_mlx() -> bool:
    if platform.system() != "Darwin":
        return False
    return _probe_python_mod("mlx.core")


def _probe_capture_cards() -> list[str]:
    sysname = platform.system()
    out: list[str] = []
    if sysname == "Linux":
        for i in range(6):
            dev = Path(f"/dev/video{i}")
            if dev.exists():
                out.append(str(dev))
    elif sysname == "Darwin":
        info = _run_quiet(["system_profiler", "SPUSBDataType"])
        for line in info.splitlines():
            low = line.lower()
            if ("capture" in low or "elgato" in low
                or "avermedia" in low or "ms2130" in low):
                out.append(line.strip())
    return out


def _probe_serial_ports() -> tuple[list[dict], list[dict]]:
    try:
        import serial.tools.list_ports as lp
    except Exception:
        return [], []
    ports = []
    arduinos = []
    for info in lp.comports():
        entry = {
            "device": info.device, "product": info.product or "",
            "manufacturer": info.manufacturer or "", "vid": info.vid, "pid": info.pid,
        }
        ports.append(entry)
        if info.vid in (0x2341, 0x2A03, 0x1B4F, 0x16C0):  # Arduino, SparkFun, Teensy
            arduinos.append(entry)
    return ports, arduinos


def _probe_adb() -> list[dict]:
    bin_ = shutil.which("adb")
    if not bin_:
        return []
    out = _run_quiet([bin_, "devices"])
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


def _probe_pcileech() -> dict | None:
    bin_ = shutil.which("pcileech")
    if not bin_:
        return None
    out = _run_quiet([bin_, "probe"], timeout=10)
    if out:
        return {"bin": bin_, "probe": out[:400]}
    return {"bin": bin_, "probe": ""}


def _probe_python_mod(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _inventory_satisfies(inv: dict, requirement: str) -> bool:
    req = requirement.lower()
    if req == "hdmi_splitter":   # self-attest
        return True
    if req == "capture_card":
        return bool(inv.get("capture_cards"))
    if req == "second_pc_gpu":
        return inv.get("cuda_available", False) or inv.get("mlx_available", False)
    if req == "second_pc":
        return True
    if req == "arduino_leonardo":
        return bool(inv.get("arduino_candidates"))
    if req == "fpga_dma_card":
        return inv.get("pcileech_device") is not None
    if req == "kmbox_net":
        return bool(inv.get("kmbox_net_endpoint"))
    if req == "uefi_board":
        return True   # cannot detect from here
    if req == "flashback_support":
        return True
    if req == "unlockable_phone":
        return bool(inv.get("adb_devices"))
    return False


def _tail(d: dict) -> dict:
    """Trim adapter result for recipe log tail storage."""
    return {k: (v if not isinstance(v, (str, bytes)) else v[:1000])
            for k, v in d.items() if k not in ("stdout", "stderr")}


def _pick_reader_backend(game: str) -> str:
    """Default to DMA on PC, android bridge on mobile. iOS uses its own
    adapter that still runs the ``android`` wire protocol under the hood
    because the iOS dylib speaks the same JSON."""
    if game in ("bgmi", "cod_mobile", "free_fire", "pubg_mobile"):
        return "android"
    if game in ("bgmi_ios",):
        return "android"    # dylib speaks the same protocol
    return "dma"


def _default_process_name(game: str) -> str:
    return {
        "valorant": "VALORANT-Win64-Shipping.exe",
        "cs2":      "cs2.exe",
        "apex":     "r5apex.exe",
        "rust":     "RustClient.exe",
        "fortnite": "FortniteClient-Win64-Shipping.exe",
        "r6_siege": "RainbowSix.exe",
        "tarkov":   "EscapeFromTarkov.exe",
        "cod_mw":   "ModernWarfare.exe",
        "pubg":     "TslGame.exe",
    }.get(game, "")


def _load_runtime_prompt() -> str:
    """Load prompts/game-cheat-ops.md next to the repo root."""
    candidate = Path(__file__).resolve().parents[2] / "prompts" / "game-cheat-ops.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    # Fallback: shipped resources path if the package was installed
    pkg = Path(__file__).resolve().parent / "resources" / "game-cheat-ops.md"
    if pkg.exists():
        return pkg.read_text(encoding="utf-8")
    return "You are the GameLab guide. Help the operator build and tune game cheats."
