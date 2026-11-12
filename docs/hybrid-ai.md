# Kaggle reasoning and local media

Use the selected Kaggle endpoint for reasoning, course study, cited retrieval,
test planning and interpretation. Local Ollama vision describes images and video
frames; local Whisper handles speech. Transcripts and inferred descriptions can
be supplied as text to the selected reasoning provider. This is local media
processing, not a promise that all derived information stays on the Mac.

`ModelTools.get_source_frame` now returns a local vision description, timestamp,
source/artifact IDs and model attribution. It never returns image bytes. Source
visibility is checked before reading or interpreting frames. Disabled/unavailable
local vision produces an error; it does not fall back to uploading the image.
The vision runtime checks the exact Ollama model metadata for vision support and
rejects cloud-backed models before transmitting image bytes. Vision requests use
a bounded 4,096-token context; keep one local model active at a time on this 8 GB Mac.

## Connecting and checking Kaggle

In **Settings → My own models**, select **Kaggle / custom AI** for an
OpenAI-compatible `/v1` server, or **Ollama on another computer** for an Ollama API.
Save the current tunnel URL, exact served model ID and connection key. Secrets
belong in the app's keychain field. Select remote inference explicitly; local-only
mode prevents it. The separate desktop мост-ИП has its own model settings.

**Save & test** now checks the compatible server's `/models` endpoint with the
saved key. A listing proves reachability and model availability, not inference
quality or native function calling. Run the bounded tool protocol check with:

```sh
backend/.venv/bin/python scripts/check_kaggle_model.py --smoke
```

This reads saved settings without modifying them. It sends two inference requests
and exposes one tool that returns a temporary random value. The first response
must call that exact tool with empty arguments; the second must use its actual
result. No website, game, course material, shell or arbitrary tool is exposed.
There are no retries or provider fallback. A notebook outage stops the check.
A passing check does not demonstrate Burp or memory-task competence.

## Improving task competence

Start with a capable hosted model and the existing cited knowledge base. Courses
become extracted text, timestamps, source-backed assertions and reviewed examples;
importing them does not update model weights. The existing specialist training
workspaces keep Burp and Cheat Engine examples separate. Export reviewed examples,
hold out entire scenarios, then compare a small LoRA adapter against the unchanged
base model. Preserve the base checkpoint and deploy a candidate only after it
improves held-out task outcomes without regressions.

Use success criteria tied to tools: valid calls and arguments, citation accuracy,
candidate elimination, process/build identity, and observed results. A guessed
health or cooldown address is not a discovery. An address matching several values
still requires semantic verification, and absolute addresses can change on restart.
Engine layouts, builds and server-authoritative state differ; no weight edit
provides a universal address finder for all games.

For cart/coupon tests, compare an authorized baseline with server responses and
the authoritative quote/order outcome. A changed DOM price, reflected input, HTTP
200 or a lower displayed total does not establish a server-accepted pricing flaw.
Use the existing disposable shop for workflow evaluation before applying checks
to a scoped target. Keep request/session evidence and approval enforcement.

The November 2, 2026 weight-space programming report is an experimental option,
not the deployment plan. Its language-model example removed a looping behavior;
the authors' non-looping Gemma experiment did not establish a reasoning gain.
That evidence does not demonstrate teaching arbitrary reverse-engineering or
web-testing skills through direct weight edits. Kaggle supplies compute; model
weights, training data, tool protocol and evaluations determine capability.

Sources: [weight-space programming authors](https://rikiyat.github.io/blog/weight-space-programming/),
[Hugging Face LoRA guide](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora),
[PortSwigger business-logic examples](https://portswigger.net/web-security/logic-flaws/examples).

## Prompt editor

**Tools → Prompt editor** provides a local, deterministic English tone editor.
It applies a short list of courteous phrase replacements, preserves quoted/code
spans and URLs, shows each change, and requires an explicit copy action. It does
not send, save or execute prompts. It does not rename harmful actions, invent
research contexts or disguise intent to обход safeguards. Arbitrary text may
remain unchanged; this is not an intent classifier or a general language model.

## Verification on November 4, 2026

- Backend: `backend/.venv/bin/python -m pytest -q backend/tests` — 609 passed,
  1 skipped. Includes provider probes, local-media isolation, training, course
  retrieval and commerce evidence checks with controlled fixtures.
- Frontend: TypeScript and `npm run build` passed; 32 routing checks passed.
  Six Playwright checks passed for the prompt editor and primary navigation,
  including 375 px, desktop and landscape layouts. These use mocked app RPCs;
  the whole browser suite and packaged native app were not revalidated here.
- Native helper: 20 tests ran, 4 skipped because external-process debugging was
  unavailable. A live helper self-test read, changed, reread and restored its
  own temporary value successfully through Mach APIs.
- Among Us: the installed app was launched. Attaching to its running process
  failed with `task_for_pid: KERN_FAILURE(5)`. No game-memory scan or modification
  succeeded; health/cooldown locations remain unverified. OS protections were
  not modified.
- Local vision: the configured 2B model passed its metadata check and identified
  `VALUE 4827` in a locally generated test image. This one inference took about
  20 seconds, including any model loading. Course interpretation accuracy was
  not evaluated. FFmpeg, Whisper and a speech model were present; audio inference
  was not exercised.
- Kaggle: live testing is pending. The saved reasoning provider was still local
  Ollama, with no enabled fallback endpoints. The remote smoke check was tested
  against controlled API fixtures, not a running Kaggle notebook.
- Training: no weights were changed. The database contained zero reviewed
  specialist training examples at the time of inspection.
