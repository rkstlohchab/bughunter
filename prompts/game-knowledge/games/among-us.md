---
title: Among Us — memory cheats (no-cooldown, wallhack, role reveal)
tags: [among-us, unity, il2cpp, innersloth, memory-write]
source: in-house
updated: 2026-11-12
lifespan_class: indefinite-local, weeks-online
---

# Among Us — the honest test target

Among Us is an ideal first-run target for memory cheats:

- **Unity IL2CPP** — same pattern as Rust and Tarkov
- **No kernel AC**. PC Steam build uses EAC post-2022, but it's a
  light EAC profile — community cheats have been live for 3+ years
  without active bans
- **Mobile builds have no AC at all** — pure sandbox-based obfuscation
- **Offline Freeplay mode has no server validation** — every cheat
  works there, period
- **Online: server validates some state** (kill cooldown IS
  server-authoritative in matchmade games; map access is not)

Perfect for testing the dylib injection path, the memory walker, the
overlay — before you risk your BGMI account.

## Platforms where GameLab can run it

| Platform | AC | Reader backend | Dylib needed? |
|---|---|---|---|
| Steam PC | EAC (light) | DMA or external RPM | No (external) |
| iOS | None | ios_memory_esp (dylib + AltStore) | Yes |
| Android | None | mobile_memory_esp (Zygisk) | Yes |
| Switch | Nintendo custom | Out of scope | — |

Fastest to test is **iOS** since dj already has the sideload path
built from BGMI. Second fastest: Steam on Mac with external RPM.

## Memory layout (Unity IL2CPP, recent 2024+ builds)

### Key classes

```csharp
class PlayerControl : NetworkedBehaviour {
    static List<PlayerControl>  AllPlayerControls;   // every player
    static PlayerControl        LocalPlayer;         // yourself
    GameData.PlayerInfo         Data;                // role + outfit + name
    PlayerPhysics               MyPhysics;           // movement
    float                       killTimer;           // ← local cooldown
    bool                        Visible;             // ← client render flag
    uint32_t                    PlayerId;
    bool                        inVent;
}

class GameData.PlayerInfo {
    string      PlayerName;
    bool        IsDead;
    bool        IsImpostor;                           // ← role reveal
    uint8_t     ColorId;
    uint8_t     HatId, SkinId, PetId;
    bool        Disconnected;
}

class ShipStatus : NetworkedBehaviour {
    static ShipStatus    Instance;
    SystemType[]         AllDoors;
    MapTypes             Type;                        // Skeld / MiraHQ / Polus / Airship / Fungle
    Vector2[]            Vents;
    LightSystem          lightSystem;                  // ← fog / vision cone
}

class PlayerPhysics : NetworkedBehaviour {
    Rigidbody2D          body;                         // body.position (x, y)
}
```

### Offsets (Unity IL2CPP via metadata)

Unity IL2CPP offsets are VERSION-STABLE per game release (unlike
compiled-C++ offsets). Each Among Us release ships a `global-metadata.dat`
file that holds class/method layout; your GameLab reader loads it once
per install.

Community dumpers:
- https://github.com/JimLiu/Il2CppDumper — Windows/Mac binary
- https://github.com/djkaty/Il2CppInspector — richer output

```bash
Il2CppDumper \
    /path/to/AmongUs.app/Payload/AmongUs.app/Frameworks/GameAssembly.dylib \
    /path/to/global-metadata.dat \
    /tmp/among_us_dump/
```

Output includes `dump.cs` (reconstructed C#) + a JSON with every field
offset. Drop it into `cheats/game_data/among_us.offsets.json`.

## The two cheats you asked for

### 1. No-cooldown kill

`PlayerControl.killTimer` is a `float` on your `LocalPlayer`. The game
re-sets it to 20.0-45.0 (varies by lobby settings) after each kill. If
you overwrite it with 0.0 every tick, the kill button is always ready.

**Offline Freeplay**: works unconditionally.

**Online matchmade**: the HOST re-sends the cooldown via RPC. Your
client resets it, the host overrides on the next tick. Effect: you
can double-tap, but you can't infinite-chain unless you ALSO patch
the RPC handler to ignore `RpcMurderPlayer`'s cooldown parameter.

**Code** (Python side, in the Among Us walker):

```python
def write_kill_cooldown_zero(reader: MemoryReader, offsets: Offsets):
    local = reader.read_u64(reader.resolve_module("GameAssembly.dylib") 
                            + offsets.require("PlayerControl_LocalPlayer"))
    if not local: return
    kill_timer_off = offsets.require("PlayerControl.killTimer")
    reader.write(local + kill_timer_off, b"\x00\x00\x00\x00")   # float 0.0
```

GameLab's `among_us_cheat` adapter runs this in a 50Hz write loop
while the recipe is active.

### 2. Wallhack / role reveal

Among Us doesn't actually hide other players through walls on the
standard mode — they're visible across rooms. What IS hidden:

- **Impostor role** (who's the impostor)
- **Impostor in vents** (vented impostors don't render)
- **Fog of war / light system** (on dark maps / sabotage events)
- **Dead-body visibility** when ghost (if you're alive)

Useful wallhacks:

**A — role reveal**. Walk `PlayerControl.AllPlayerControls`, read each
`Data.IsImpostor`, color the ESP box red if impostor:

```python
def walk_players_with_roles(reader, offsets):
    base = reader.resolve_module("GameAssembly.dylib")
    all_ptr = reader.read_u64(base + offsets.require("PlayerControl_AllPlayerControls"))
    items = reader.read_u64(all_ptr + 0x10)   # Il2CppList._items
    count = reader.read_i32(all_ptr + 0x18)
    out = []
    for i in range(count):
        pc = reader.read_u64(items + 0x20 + i * 8)
        if not pc: continue
        data_ptr = reader.read_u64(pc + offsets.require("PlayerControl.Data"))
        is_imp   = reader.read_u8(data_ptr + offsets.require("PlayerInfo.IsImpostor"))
        is_dead  = reader.read_u8(data_ptr + offsets.require("PlayerInfo.IsDead"))
        name_ptr = reader.read_u64(data_ptr + offsets.require("PlayerInfo.PlayerName"))
        name = reader.read_cstr(name_ptr + 0x14, 32) if name_ptr else ""
        phys = reader.read_u64(pc + offsets.require("PlayerControl.MyPhysics"))
        body = reader.read_u64(phys + offsets.require("PlayerPhysics.body"))
        pos  = reader.read_vec3(body + offsets.require("Rigidbody2D.position"))
        out.append({"name": name, "impostor": bool(is_imp),
                    "dead": bool(is_dead), "pos": pos})
    return out
```

Overlay colors each player's box red (impostor) or blue (crew) — pure
client-side rendering, no network.

**B — light system off**. `ShipStatus.Instance.lightSystem.PrimaryRadius`
= 10000.0f makes the whole map always lit for you. Client-side only;
server doesn't check.

**C — vent visibility**. `PlayerControl.inVent` boolean — if ANY
remote player's `inVent=true`, they stop rendering. Writing it to
false for all remotes (on your client) makes vented impostors visible
to you. Server authoritative on vent transitions (impostor can't
actually teleport), but client-side visibility is fully controllable.

## Per-platform workflow

### iOS (dj's path, dylib + AltStore)

Same patch_ipa.sh + sideload.sh flow as BGMI. Use the already-built
dylib in a per-game mode:

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "patch_ipa",
  ipa_in: "~/Downloads/AmongUs-stock.ipa",
  ipa_out: "~/Downloads/AmongUs-gamelab.ipa"
}
```

Then push the Among Us offsets instead of BGMI:

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "push_offsets",
  game: "among_us",
  port: 65510
}
```

Among Us iOS has no anogs — all the bundle-hash/antidetect hooks are
no-ops (they look for libanogs.A.dylib; absent in Among Us). Your
dylib runs clean.

### Steam PC (Mac M1 — your daily driver)

Among Us on Mac Steam uses an older EAC build that runs usermode-only
on macOS. External RPM via `task_for_pid` requires the `com.apple.security.
cs.debugger` entitlement — not available without jailbreak.

Pragmatic Mac path: **DMA FPGA** against your gaming rig if it's
Windows. Or dual-boot Linux / Windows → run the Windows build.

Linux Steam/Proton build of Among Us runs with EAC-Linux (usermode).
External LD_PRELOAD injection works there — easier than Windows.

### Android (if you have a rooted Android alongside)

Same Zygisk pattern as BGMI, different offsets. Add Among Us to the
DenyList, push Among Us offsets.

## GameLab adapter

`backend/bughunter/op_adapters/among_us_cheat.py` ships with these
modes:

| Mode | Effect |
|---|---|
| `start_wallhack` | box every player with role color + fog-off |
| `start_no_cooldown` | 50Hz write of `killTimer=0` on LocalPlayer |
| `start_all` | both of the above + role name ESP + dead-body reveal |
| `stop` | SIGTERM the write loop(s) |
| `status` | state + last-write success |

All driven by the universal Frame — same overlay window draws role-
colored boxes using the Among Us walker's team-as-role mapping.

## Lifespan

- **Freeplay (offline) mode**: works forever. No server, no AC, no
  bans possible.
- **Online quickplay (public lobbies)**: EAC on PC may flag the no-
  cooldown RPC pattern after prolonged use. Mobile and Freeplay don't
  have this concern. Typical lifespan on a public account with light
  use: weeks.
- **Private lobbies**: host-authoritative. Your cheats affect only
  what your client sees; the host validates kills + votes. Wallhack
  = unlimited. No-cooldown = you double-tap but can't infinite-chain.

## First-test checklist

1. Install Among Us on your iPhone (free).
2. Extract the IPA via iMazing (same tool as BGMI).
3. Build the dylib (same dylib — it's game-agnostic).
4. Patch the IPA with the dylib.
5. Sideload via AltStore.
6. Launch Among Us. Confirm the dylib logged on Console.app.
7. From bughunter Chat: "start among us cheat on my iphone, no cooldown
   and wallhack, udid <paste>".
8. The silent routing picks it up → GameLab guide → runs the whole
   stack.
9. Open Among Us → Freeplay → walk around → your overlay should show
   role boxes, map should be fully lit, kill button should always be
   ready (set yourself as impostor via the Freeplay menu).

No account at risk — Freeplay is offline.
