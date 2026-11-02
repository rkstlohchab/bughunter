# P8 — Advanced Capability Workbench

This release extends P7 without removing the P0–P7 knowledge, memory, desktop and Burp work.

## Reviewed model fine-tuning (MLX LoRA)

Open **Settings → P8 Advanced capabilities**. Create a reviewed, training-permitted dataset with an independent evaluation split in **Knowledge → Training data** and export it as a P2 dataset ZIP. The ZIP must remain under the BugHunter data directory's `exports` folder. P8 lists eligible exports automatically.

On an M1 or newer Apple Silicon Mac, install `mlx-lm[train]` **in the Python environment that runs the backend**, then choose an MLX-compatible local or Hugging Face model, select a dataset, and select **Train local LoRA**. P8 uses `python -m mlx_lm lora --model ... --train --data ...`, prepares `train.jsonl` and `valid.jsonl`, retains the original P2 source manifest hash, writes logs into a local private directory, and checks for `adapters.safetensors` before reporting a completed adapter. Stop is supported. Jobs interrupted by a restart are labeled interrupted, never completed.

The model is not automatically changed in Settings when training finishes. You must evaluate the adapter and choose a compatible inference route before use. Large models may exceed M1 memory; start with a small MLX-compatible model. Neither dataset export nor a dry-run updates model weights. The training job itself was not run in the Linux build environment.

## Radar on an operator-selected process

The original bundled lab remains:

```bash
python3 scripts/run_esp_lab_demo.py
```

To read entities from an accessible offline process with an **already observed and verified** structure layout:

```json
{
  "pid": 12345,
  "entity_base": "0x12345000",
  "entity_layout": {
    "count": 4,
    "stride": 24,
    "fields": [
      {"name": "x", "type": "f32", "offset": 0},
      {"name": "y", "type": "f32", "offset": 4},
      {"name": "id", "type": "i32", "offset": 8},
      {"name": "health", "type": "i32", "offset": 12},
      {"name": "team", "type": "i32", "offset": 16}
    ]
  }
}
```

Save it as `entity-layout.json` with the **real** PID and values, then:

```bash
python3 scripts/run_esp_lab_demo.py --layout ./entity-layout.json
```

The service binds only to localhost and reads actual memory through the Mac helper. A nonexistent process, invalid layout, denied memory access, or missing coordinates raises a visible failure. It does not discover unknown offsets, inject a display into a game, bypass protections, or turn 2D radar coordinates into game-view ESP automatically. A game restart may require a new PID and re-resolved entity address.

## Evidence-based website coverage

In **Settings → P8 Advanced capabilities**, choose a project and click **Review current project**. P8 correlates actual captured traffic, checkout-looking URLs, and staging test receipts. Missing access-control or transaction evidence is explicitly marked unverified. Existing Burp and staging quote regressions remain available in their own panels. The P8 overview does not send new requests or perform arbitrary production checkout actions.

The P3 research browser continues to pause at CAPTCHA/login interruptions for manual completion; no automatic third-party CAPTCHA bypass is implemented.

## AI tools

The `get_advanced_capabilities` read-only tool lets a configured model query MLX readiness, job state and integration limits for its own project. The model cannot start LoRA training, claim verification, or attach to memory using this tool.

## Test and build

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/verify_p8_advanced.sh
npm run desktop:build
```

The verification script runs the P8/P7 tests and prior P0–P6 focused suites. Full Mac compilation, actual training, Burp connectivity, and game memory attachment still require Mac testing.
