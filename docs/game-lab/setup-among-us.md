# Setup — Among Us (no-cooldown kill + wallhack)

Your first-run test target. Among Us is Unity IL2CPP, has no kernel
anticheat on mobile, and runs the same dylib injection path you're
about to use for BGMI iOS. If this works end-to-end, every component
of the GameLab stack is validated.

**Freeplay (offline) is unconditional** — no server, no AC, no ban
possible. Cheats you write there are guaranteed to run; use Freeplay
for the first session.

## Fastest path (iOS, reuses your BGMI sideload)

### 1. Install Among Us on your iPhone

Free from the App Store. Launch once to let it download its asset
bundles, close it.

### 2. Extract the IPA via iMazing

Same tool you used for BGMI. Manage Apps → Among Us → Extract IPA →
save as `~/Downloads/AmongUs-stock.ipa`.

### 3. Build the dylib (if you haven't already)

```
gamelab.start_recipe adapter=ios_memory_esp config={mode:"build_dylib"}
```

Same `gamelab_memory.dylib` — the dylib is game-agnostic.

### 4. Patch the Among Us IPA

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "patch_ipa",
  ipa_in:  "/Users/you/Downloads/AmongUs-stock.ipa",
  ipa_out: "/Users/you/Downloads/AmongUs-gamelab.ipa"
}
```

### 5. Sideload

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "sideload",
  ipa_out: "/Users/you/Downloads/AmongUs-gamelab.ipa",
  via: "altstore"
}
```

AltStore re-signs with your Apple ID and installs. Same 7-day refresh
rules as BGMI.

### 6. Dump Among Us offsets with Il2CppDumper

Among Us on iOS ships `GameAssembly.dylib` + `global-metadata.dat`
inside the app bundle. Extract them from the stock IPA and dump the
offsets once per game release:

```bash
# Unzip the stock IPA to find the two files
unzip ~/Downloads/AmongUs-stock.ipa -d /tmp/au
# They sit under Payload/Among Us.app/Frameworks/UnityFramework.framework/
find /tmp/au -name global-metadata.dat
find /tmp/au -name "libil2cpp.*"

# Clone Il2CppDumper
git clone https://github.com/Perfare/Il2CppDumper /tmp/il2d
cd /tmp/il2d
dotnet build -c Release
./bin/Release/Il2CppDumper /tmp/au/.../libil2cpp.so  /tmp/au/.../global-metadata.dat  /tmp/au_dump

# Read out the offsets we care about from /tmp/au_dump/dump.cs
# Fill them into:
#   <data_dir>/gamelab_offsets/among_us.json
```

The dumper is the same tool the community uses for every Unity game.
It's a one-off per Among Us version; pin the output.

### 7. Push offsets into the dylib on your iPhone

Plug iPhone to Mac, launch the patched Among Us. Then:

```
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "forward", udid: "<your-udid>", port: 65510
}
gamelab.start_recipe adapter=ios_memory_esp config={
  mode: "push_offsets", game: "among_us", port: 65510
}
```

The dylib caches the offsets; it'll use them until you push a new set.

### 8. Start the cheat

Pick your features. From the Chat panel, type:

> "start among us cheat on my iphone, no cooldown and wallhack, udid <paste>"

The silent routing picks it up → GameLab guide starts both pipelines:

- `memory_esp` reader + universal overlay (team-colored boxes per role,
  dead players ghosted)
- `among_us_cheat` write-loop (50Hz killTimer=0 on LocalPlayer, fog-off
  one-shot)

Or explicitly:

```
gamelab.start_full_kit project_id=<id>
  features={memory_esp: true, draw_box: true, draw_name: true,
            chams_mode: "team"}
  hid={ios_udid: "<udid>"}

gamelab.start_recipe adapter=among_us_cheat config={
  mode: "start", feature: "all",
  reader_backend: "android",       # iOS dylib speaks the android protocol
  mobile_endpoint: "127.0.0.1:65510"
}
```

### 9. First session — Freeplay

In Among Us:

1. Local → Freeplay
2. The game spawns you solo on a map with bots
3. Walk around. The overlay window on your Mac should show role-colored
   boxes around every player + name tag + alive/dead state
4. Hit the Admin panel or Vitals — your map should be fully lit (fog
   disabled)
5. Set yourself as impostor in Freeplay settings → every player box
   should glow red (impostor view) except yours
6. Kill button should always be ready — rapid-tap to confirm no
   cooldown

No account at risk. No network traffic. If everything works here, the
whole stack is proven.

### 10. Testing online

- **Online quickplay (public lobbies)**: most Among Us cheats run here
  unchecked. Innersloth's EAC on PC is light; mobile has none. Expect
  weeks of lifespan before a wave ban.
- **Private lobbies** (your own code): host-authoritative. Your kill
  cooldown write makes YOUR button clickable; the host still enforces
  the actual cooldown on kill-RPCs. Effect: you can double-tap, but
  can't infinite-chain unless the host also has a modified client.
- **Ranked**: same host-authoritative check. Behavioral opsec stays
  relevant (don't kill 10 people in 20 seconds).

## Fastest path (Mac via Steam — if you prefer)

Among Us on Mac Steam uses an older EAC build in userland only.
External reads via DMA or Wine-Proton-LD_PRELOAD both work.

```
gamelab.start_recipe adapter=memory_esp config={
  mode: "start", game: "among_us",
  reader_backend: "rpm",          # external RPM on Windows / Mac
  process_name: "Among Us.app"
}
gamelab.start_recipe adapter=among_us_cheat config={
  mode: "start", feature: "all",
  reader_backend: "rpm",
  process_name: "Among Us.app"
}
```

On macOS, external RPM needs the `com.apple.security.cs.debugger`
entitlement — you don't have it without a dev-signed binary with
that entitlement. Practical mac path: dual-boot Linux with Proton
+ LD_PRELOAD injection, or test iOS.

## Troubleshooting

- **Overlay shows nothing** → push_offsets wasn't called; the dylib
  has no field offsets to walk. Re-run step 7.
- **Kill cooldown doesn't reset** → the walker's `write_kill_cooldown_zero`
  requires the write-path on the MemoryReader. The iOS bridge
  supports write via a `{"cmd":"write"}` command — verify by sending
  `{"cmd":"ping"}` and checking the dylib's Console.app output.
- **Fog won't disable** → `ShipStatus_Instance` offset is wrong
  (ShipStatus gets instantiated when you load a map). Enter Freeplay
  FIRST (so ShipStatus exists), then start the recipe.
- **Overlay shows role boxes but names are blank** → `PlayerInfo.PlayerName`
  offset wrong. Re-dump via Il2CppDumper; drift between Innersloth
  releases is 3-6 fields max.

## Offset re-derivation on Among Us updates

Innersloth ships roughly one update per 2-3 months. Per update:

1. Download the new stock Among Us IPA (iMazing / 3uTools).
2. Re-run Il2CppDumper against the new `libil2cpp.so` +
   `global-metadata.dat`.
3. Diff the old and new `dump.cs` for the fields in the offsets
   template.
4. Update `<data_dir>/gamelab_offsets/among_us.json`.
5. Re-push: `adapter=ios_memory_esp mode=push_offsets game=among_us`.

No dylib rebuild needed. No re-sideload needed. Just an offsets
update. This is why Among Us is such a clean first target.
