# ESP + Aimbot renderer — `cheats/esp/`

Generic DirectX 11 internal overlay + aimbot module. Game-agnostic by
design — the operator derives per-game offsets per build using the
methodology in [`docs/anticheat_methodology.md`](../../docs/anticheat_methodology.md)
and drops them into [`game_config.h`](game_config.h.example).

## Files

| File | Role |
|---|---|
| `world_to_screen.h` | Projection math — `WorldToScreen`, `WorldBoundingBox`, `AngleFov`. Header-only, engine-agnostic, DirectX screen-space convention. |
| `game_config.h.example` | **Copy to `game_config.h` and fill offsets.** Three templates: Unity/Il2Cpp (BGMI / PUBG Mobile), Source 2 (CS2), UE4 (Valorant / any UE4 shipping). Placeholder offsets — derive the real ones per patch. |
| `overlay_internal_dx11.cpp` | Internal DX11 ESP — MinHook `Present` hook + ImGui draw list. Build as `esp.dll` and inject. F11 to unload cleanly, INSERT for config menu. |
| `aimbot.cpp` | Target selection (FOV cone + distance + team check), head-bone aim point, view-angle delta with smoothing, `SendInput` mouse output. Includes reference frame for routing the same dx/dy through a hardware HID bridge (Arduino Leonardo / KMBox / Interception) for anti-detection on live services. |

## Build

From a Visual Studio x64 Developer Command Prompt:

```bat
cl /std:c++17 /LD /EHsc /MD ^
    overlay_internal_dx11.cpp aimbot.cpp ^
    /I<minhook>\include /I<imgui>\include ^
    /Fe:esp.dll ^
    <minhook>\lib\libMinHook.x64.lib ^
    imgui.lib imgui_impl_win32.lib imgui_impl_dx11.lib ^
    d3d11.lib dxgi.lib user32.lib gdi32.lib
```

Dependencies (all public, headers + libs):

- [MinHook](https://github.com/TsudaKageyu/minhook) — public
- [Dear ImGui](https://github.com/ocornut/imgui) + `imgui_impl_win32` + `imgui_impl_dx11` — public

## Per-game setup

1. Pick the matching template in `game_config.h.example` (Unity / Source 2 / UE4).
2. Copy to `game_config.h`.
3. Derive real offsets for the current game build. Methods per engine:
   - **Unity / Il2Cpp (BGMI, PUBG Mobile)**: run `Il2CppDumper` against
     `libil2cpp.so` + `global-metadata.dat`. Locate the game's primary
     actor/character class in the dump; the fields' offsets in the
     generated `dump.cs` are what you plug in.
   - **Source 2 (CS2)**: run a schema dumper (e.g. `source2gen`) against
     `cs2.exe`. Fields come from `CBasePlayerPawn`, `CCSPlayerController`,
     `CEntitySystem`. Offsets drift across updates — re-dump per patch.
   - **UE4 (Valorant, PUBG, Fortnite builds)**: scan the shipping exe for
     UE4 reflection globals (`GNames`, `GWorld`, `GObjects`). Use a UE4
     SDK generator against the specific build; actor hierarchy + `UProperty`
     offsets appear in the generated SDK.
4. Toggle render options in `GameConfig` (boxes / names / health /
   distance / lines / bones / team check / max distance).
5. Build the DLL. Inject via whichever loader fits your target process
   (manual map / LoadLibrary / BYOVD-mapper / etc. — see
   `docs/anticheat_methodology.md` §3.7 driver-loading decision tree).

## Injection + anticheat realism

The renderer runs cleanly against:

- Offline / single-player games
- Games you own the server for (dedicated local server, LAN)
- Authorized research environments (your own game, operator-owned test
  server with written authorization)

For live Vanguard / VAC / ACE titles, this code renders fine in theory
but the injection surface (module walk, handle open, memory read
pattern, `Present` hook) is what the AC detects. Getting the ESP to run
in a live match against those three services is a separate engineering
problem: the per-build derivation workflow in
`docs/anticheat_methodology.md` §3 is where that work happens. This
renderer is the output layer that sits on top of whatever surface
survives detection.

## Aimbot hardware-HID path

`SendInput` is visible to any userland input hook. For live-service
anti-detection, route the same `(dx, dy)` through an external HID
device so the cursor movement arrives at the OS as real hardware
input. The file `aimbot.cpp` has the reference serial frame (Arduino
Leonardo as HID mouse) — operator wires an Arduino / KMBox / similar,
opens the COM port, writes 7 bytes per aim tick, and the aim math
upstream is unchanged.

## Tuning notes

- **Box size** (`hw=25, hh=90, hd=25` in `RenderESP`) is in game units.
  Unity defaults to ~1 unit per meter; Source engines use inches
  (~2.54 units/cm); UE4 uses centimeters. Tune per target.
- **Smoothing** of 4.0 is comfortable aim-assist; 1.0 is snap. Higher
  values produce slower pulls that look more human (and pass VACnet /
  behavioral detectors better).
- **FOV** of 6° is a comfortable cone; lower is more selective.
- **Mouse sensitivity scale** in `aimbot.cpp` (`counts_per_deg_yaw` /
  `counts_per_deg_pitch`) is hardware-dependent — measure yours on
  your rig once and set it.
- The head-bone index in the aimbot (`head_bone_index = 8`) matches
  common Source 2 / Valve convention. For UE4 / Unity the index is
  per-skeleton — check your SDK dump.

## Scope reminder

See [`docs/anticheat_methodology.md`](../../docs/anticheat_methodology.md)
for architecture + detection vectors + per-build derivation. This
directory is the rendering + aim-math layer. Target selection and
authorization are the operator's.
