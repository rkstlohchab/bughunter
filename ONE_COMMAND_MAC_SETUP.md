# BugHunterClaudex — one-script Mac setup

This archive has a new `setup-mac.sh` at the project root.

## Quick start

1. Extract the ZIP. Open Terminal in the extracted `bughunter` folder.
2. Run **once**:

   ```bash
   bash setup-mac.sh
   ```

3. When the installer prints `Core setup complete!`, build the app:

   ```bash
   npm run desktop:build
   ```

The `.app` will be at `src-tauri/target/release/bundle/macos/BugHunterClaudex.app`.
The `desktop:build` command is `tauri build --bundles app`. The `beforeBuildCommand`
now checks for a current, architecture-specific Python sidecar and regenerates
it if backend code changed. You can also run `npm run tauri build -- --bundles app`.

## What is set up by the script

- macOS Command Line Tools check (Swift compiler and native build tools)
- Homebrew (if missing, using official interactive install)
- Node/npm, Rust/Cargo, uv, Python 3.12, pkg-config and JDK 21
- Backend virtual environment and Python dependencies
- Frontend packages from the existing `package-lock.json`
- Managed Playwright Chromium
- Local Swift Accessibility helper compiled from project source
- Best-effort optional FFmpeg, Tesseract, whisper.cpp, Ollama, JADX, Apktool,
  Nuclei and Subfinder installation
- Best-effort compilation of the Burp Montoya extension JAR from source;
  the JAR must be added to Burp Suite manually
- PyInstaller backend executable for the Mac's Rust host architecture;
  included in the Tauri bundle rather than requiring Python on end-user systems
- Frontend production build check

Logs are under `.setup-logs/`. Rerunning the script is supported: Homebrew
formulae already installed are skipped, and npm/uv use their normal caches.

## Options

- `bash setup-mac.sh --with-models` downloads Qwen3-VL 2B via Ollama and a
  Whisper base model (`~/Library/Application Support/BugHunterClaudex/models/ggml-base.bin`).
  These are not downloaded by default because weights are large and Qwen may
  already be installed on your Mac.
- `bash setup-mac.sh --core-only` skips optional research tools and Burp extension.
- `bash setup-mac.sh --skip-burp` skips only the extension build.
- `bash setup-mac.sh --dry-run` prints the plan without changes.
- `bash setup-mac.sh --help` prints usage.

## Manual steps that cannot be automated safely

- macOS may show an Apple Command Line Tools dialog. Complete it and rerun
  the script if instructed.
- In System Settings → Privacy & Security, grant necessary permissions to
  the app/helper after launch. A shell script cannot silently grant Accessibility
  or Screen Recording permissions.
- Install/licence Burp Suite Professional yourself, then load the generated
  `integrations/burp/build/BugHunterBurpBridge.jar` via Burp Extensions.
- Select your downloaded Ollama model and Whisper path in the app's Settings.
- Windows-only Cheat Engine builds may require Windows/Wine/CrossOver;
  installing Mac dependencies does not make arbitrary Windows processes native.
- Model-provider API credentials remain yours to configure.

## Troubleshooting

If the setup stops, read the printed phase and `.setup-logs/setup-*.log`,
fix the issue, then rerun `bash setup-mac.sh`. The final success message
appears only after all mandatory setup steps and checks pass.

The script and archive are source-level tested here; a full macOS build must
be performed on an actual Mac with internet access. The script cannot guarantee
that all commercial tools and games work on the host.
