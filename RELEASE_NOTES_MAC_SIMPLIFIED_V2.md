# BugHunterClaudex — Mac settings, knowledge reader and memory-helper fixes

## Visible changes

- **Knowledge is for learning.** Import, search and read resources. Video processing there only shows recording timelines and allows pause/resume/reprocess. Ollama, Whisper, FFmpeg and Qwen model setup, configuration, downloads, status and rechecks moved to Settings -> Video AI setup.
- **One document reads as one document.** Backend still indexes separate text passages for retrieval, citations, and corrections. The source reader now combines them as one continuous document, and collapses individual passage editing under an optional section. These are not separate imported files.
- **Memory tasks are simpler.** The default memory helper shows a clear explanation, Connect/Disconnect, progress/results, a task textbox, and approvals. Technical pointer and saved-method controls are collapsed under optional advanced sections. Technical bridge configuration is no longer in Tasks.
- **Memory setup belongs to Settings.** On Mac, the built-in memory reader is different from Cheat Engine. Connection to the reader never proves Cheat Engine is installed. Settings -> Memory helper selects backend, checks environment and allows optional technical setup. Settings -> Mac access provides separate checks for Screen Recording and Accessibility access; process access is still protected by macOS.
- **AI connection is shared for supported models.** Memory-agent sessions reuse the selected Chat AI connection (Kaggle/OpenAI-compatible, Ollama, OpenAI or DeepSeek). The app supplies the model credential from the OS keychain only for the active bridge process; no second API key URL/model field in Tasks and new saves strip that credential from the legacy bridge JSON. Claude Code and Anthropic native APIs do not expose the required OpenAI-compatible endpoint; use a supported model in Settings for this helper.
- **Cheat Engine detection improved.** Also checks the normal CrossOver Bottles folder, plus standard app bundles, PATH, Spotlight, and Wine paths. A manual application-file locator remains available. Burp Suite detection requires actual Burp evidence; generic commands do not qualify.
- **Chat suggests missing video tools.** If attached video/audio requires local FFmpeg/Whisper/Qwen, Chat shows a contextual link to Settings rather than presenting install buttons in Knowledge.
- Removed a broken redundant tool-check button from Tasks.

## Plain-English glossary

- **Find where a number is stored:** look in your own offline program for the memory address of a visible value, such as a score of 100.
- **Saved pointer candidate:** a *possible* way to find the same value again. It is not confirmed until checked.
- **Check after restart:** close and reopen your test program to see whether that saved method still locates the value.
- **Search previous notes:** optionally refer to methods already in your Knowledge library.

## Autonomy boundaries

The app can independently route Chat intents and perform existing bounded, approved workflows, but **this is not a verified, fully autonomous general hacking agent**. Website operations remain limited to specifically authorized scopes and audited access, and process attachment/writes still require explicit user authorization. Auto-detected installed apps are not automatically accessible; macOS access may be requested only when needed, and protected apps can refuse connections.

## Verification performed in the build environment

- Source archive and Python bytecode compilation completed.
- TypeScript source parsed with zero parser diagnostics using the locally available TypeScript compiler.
- Focused Python tests for tool detection and CrossOver passed.
- The complete Tauri/Rust/macOS build and end-to-end Kaggle/Whisper/Qwen execution **could not** be tested in this Linux build environment. npm dependencies and Rust toolchain were unavailable for a full build. These remain on-device verification tasks.

## Start on your Mac

```bash
cd ~/Downloads
unzip -o BugHunterClaudex-Mac-Easy-UI-Memory-Settings.zip
cd bughunter
python3 scripts/setup.py
npm run desktop:dev
```

Always back up the original app's data directory before upgrading. Do not copy secrets or memory captures into issue reports. Backend source code and configuration are included, not any installed AI model weights.
