#!/usr/bin/env bash
# BugHunterClaudex: one-shot macOS (Apple Silicon or Intel) source-build setup.
# Run from any directory: bash /path/to/bughunter/setup-mac.sh
# Installation may prompt for macOS/Homebrew authorization. No permission is silently granted.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DRY_RUN=0
INSTALL_OPTIONAL=1
INSTALL_MODELS=0
BUILD_BURP=1
BUILD_SIDECAR=1
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      cat <<'HELP'
Usage: bash setup-mac.sh [options]

Installs Homebrew (if absent), Apple toolchain prerequisites, Node, Rust, uv,
Python 3.12, backend + frontend dependencies, Chromium, and the native
macOS control helper. Builds the Python backend executable used by Tauri.
Also attempts optional research tools and the Burp extension; failures in
optional components are reported without hiding them.

Options:
  --with-models     Also download Qwen3-VL 2B and Whisper base model weights
  --core-only       Skip optional research tools and Burp extension
  --skip-burp       Skip building the optional Burp extension
  --skip-sidecar    Skip freezing the backend (not suitable for release build)
  --dry-run         Print the setup plan without installing anything
  --help            Show this help

After successful setup:
  npm run desktop:build
Or, equivalently:
  npm run tauri build -- --bundles app
HELP
      exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --core-only) INSTALL_OPTIONAL=0; BUILD_BURP=0 ;;
    --with-models) INSTALL_MODELS=1 ;;
    --skip-burp) BUILD_BURP=0 ;;
    --skip-sidecar) BUILD_SIDECAR=0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ((DRY_RUN)); then
  cat <<'PLAN'
Dry run — no modifications will be made.
  1. Check macOS, Apple Command Line Tools, Homebrew and PATH.
  2. Install build tools: node, rust, uv, python@3.12, pkg-config, openjdk@21.
  3. Install optional CLI research tools: ffmpeg, tesseract, whisper.cpp,
     ollama, jadx, apktool, nuclei, subfinder; commercial and heavyweight apps are not installed automatically.
  4. Install backend Python packages, frontend npm packages, Chromium.
  5. Install Vercel agent-desktop for optional richer Mac computer control.
  6. Compile native macOS Accessibility helper from included Swift source.
  7. Build optional Burp Montoya extension against official API.
  8. Build PyInstaller backend sidecar for current Mac architecture.
  9. Write setup summary. Then run: npm run desktop:build
PLAN
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This installer must be run on macOS; current OS is $(uname -s)." >&2
  exit 2
fi
if [[ "$EUID" -eq 0 ]]; then
  echo 'ERROR: Do not run this script as root or with sudo. Homebrew will ask for permission if needed.' >&2
  exit 2
fi

LOG_DIR="$ROOT/.setup-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
PHASE='Starting'
OPTIONAL_WARNINGS=()
error_report() {
  code=$?
  echo
  echo "SETUP FAILED while: $PHASE (exit $code)"
  echo "Log: $LOG_FILE"
  echo 'You can rerun the same script after fixing the issue. It skips already installed packages.'
  exit "$code"
}
trap error_report ERR

step() { PHASE="$1"; printf '\n\033[1;36m==> %s\033[0m\n' "$PHASE"; }
warn() { OPTIONAL_WARNINGS+=("$1"); echo "OPTIONAL WARNING: $1"; }
have() { command -v "$1" >/dev/null 2>&1; }

step 'Checking Apple development tools'
if ! xcode-select -p >/dev/null 2>&1 || ! have swiftc; then
  echo 'Apple Command Line Tools are required to compile the Swift native helper.'
  xcode-select --install || true
  echo 'Complete the Apple Command Line Tools installation dialog, then rerun: bash setup-mac.sh'
  exit 3
fi

step 'Checking Homebrew'
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
elif have brew; then
  eval "$(brew shellenv)"
else
  echo 'Installing Homebrew using the official installer (macOS may prompt for administrator permission).'
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    echo 'Homebrew installation did not expose brew on PATH.' >&2; exit 4
  fi
fi

# Export PATH before invoking installers, including Homebrew keg-only Java 21.
export PATH="$(brew --prefix)/bin:$(brew --prefix)/sbin:$HOME/.cargo/bin:$PATH"
# Make tools available in future Terminal sessions, especially /opt/homebrew on M1/M2/M3.
if [[ "${SHELL:-}" == */zsh ]] && [[ -n "$(brew --prefix)" ]]; then
  BREW_EXE="$(brew --prefix)/bin/brew"
  PROFILE="$HOME/.zprofile"
  if [[ ! -f "$PROFILE" ]] || ! grep -Fq '# BugHunterClaudex Homebrew toolchain' "$PROFILE"; then
    printf '\n# BugHunterClaudex Homebrew toolchain\neval "$(%s shellenv)"\n' "$BREW_EXE" >> "$PROFILE"
    echo "Enabled Homebrew in future zsh sessions: $PROFILE"
  fi
fi
brew_formula() {
  local name="$1"
  if brew list --formula --versions "$name" >/dev/null 2>&1 && [[ -n "$(brew list --formula --versions "$name" 2>/dev/null)" ]]; then
    echo "Already installed: $name"
  else
    brew install "$name"
  fi
}

step 'Installing required build dependencies'
for formula in node rust uv python@3.12 pkg-config openjdk@21; do
  brew_formula "$formula"
done
export PATH="$(brew --prefix openjdk@21)/bin:$(brew --prefix python@3.12)/bin:$PATH"
# New terminal sessions will discover the tools, including on Apple Silicon.
for command_name in node npm uv rustc cargo python3 swiftc javac; do
  if ! have "$command_name"; then
    echo "Required executable not found after installation: $command_name" >&2
    exit 5
  fi
done

if ((INSTALL_OPTIONAL)); then
  step 'Installing optional analysis tools'
  # These make the app more useful, but are not prerequisites for a Tauri build.
  for formula in ffmpeg tesseract whisper.cpp ollama jadx apktool nuclei subfinder; do
    if ! brew_formula "$formula"; then
      warn "Could not install $formula. Configure it later in Settings if needed."
    fi
  done
  # Ghidra is graphical and may be large; install only if it is already installed
  # or the user explicitly opts in. Avoid installing a large unsolicited app.
  echo 'Ghidra, Burp Suite Professional and Cheat Engine are not silently installed.'
fi

step 'Installing Python backend, frontend and Chromium'
# This uses uv's managed Python 3.12; the host system Python is not modified.
python3 "$ROOT/scripts/setup.py"

step 'Installing optional Vercel agent-desktop control engine'
# Pin to a verified public release. An installation error does not remove the built-in helper.
if command -v agent-desktop >/dev/null 2>&1 && agent-desktop version >/dev/null 2>&1; then
  echo 'Already installed: Vercel agent-desktop'
elif ! npm install --global agent-desktop@1.0.2; then
  warn 'Vercel agent-desktop installation failed. The built-in Mac control engine remains available.'
fi

step 'Compiling native macOS Accessibility helper' 
bash "$ROOT/scripts/build_mac_axbridge.sh"

if ((BUILD_BURP)); then
  step 'Building Burp extension (optional)'
  if ! bash "$ROOT/scripts/build_burp_bridge.sh"; then
    warn 'Burp extension build failed. The app can still be built; see scripts/build_burp_bridge.sh.'
  fi
fi

if ((INSTALL_MODELS)); then
  step 'Downloading optional local AI model weights'
  if have ollama; then
    # Ollama's service is local to the user's Mac. It needs to be started for model downloads.
    if ! ollama list >/dev/null 2>&1; then
      brew services start ollama || true
      sleep 3
    fi
    if ! ollama pull qwen3-vl:2b; then warn 'Could not download Qwen3-VL 2B; use Models in Settings.'; fi
  else
    warn 'Ollama is unavailable; skipped Qwen3-VL download.'
  fi
  MODEL_DIR="$HOME/Library/Application Support/BugHunterClaudex/models"
  MODEL_PATH="$MODEL_DIR/ggml-base.bin"
  mkdir -p "$MODEL_DIR"
  if [[ ! -s "$MODEL_PATH" ]]; then
    # Download to a temporary file, and only install on success.
    if curl --fail --location --retry 2 --proto '=https' --tlsv1.2 \
      'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin' \
      -o "$MODEL_PATH.partial"; then
      mv "$MODEL_PATH.partial" "$MODEL_PATH"
    else
      rm -f "$MODEL_PATH.partial"
      warn 'Could not download Whisper model. Set its location manually in Settings.'
    fi
  fi
  echo "Whisper model location: $MODEL_PATH (choose this file in Settings if required)."
fi

if ((BUILD_SIDECAR)); then
  step 'Bundling Python backend for the Tauri .app'
  "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/build_backend.py"
fi

step 'Checking build readiness'
npm run build
TRIPLE="$(rustc --print host-tuple)"
if ((BUILD_SIDECAR)) && [[ ! -x "$ROOT/src-tauri/binaries/bughunter-backend-$TRIPLE" ]]; then
  echo "Missing bundled Python backend for $TRIPLE" >&2
  exit 6
fi

printf '\n\033[1;32mCore setup complete!\033[0m\n'
echo "Host: $(uname -m) | Tauri target: $TRIPLE"
echo "Log: $LOG_FILE"
if ((${#OPTIONAL_WARNINGS[@]})); then
  printf 'Optional components needing attention:\n'
  printf '  - %s\n' "${OPTIONAL_WARNINGS[@]}"
fi
cat <<'DONE'

Now build your Mac application with ONE command:
    npm run desktop:build

Output:
    src-tauri/target/release/bundle/macos/BugHunterClaudex.app

Burp Professional requires your own license, and its compiled extension must be
loaded from integrations/burp/build/BugHunterBurpBridge.jar in Burp Extensions.
Apple Accessibility and Screen Recording permissions must be granted manually.
Commercial apps and local AI model weights are not automatically activated.
DONE
