# BugHunterClaudex Kaggle launcher

Import `notebooks/BugHunterClaudex_Kaggle.ipynb` into Kaggle, or paste the **entire**
`scripts/kaggle_launcher.py` into one notebook code cell. Replace the old launcher
and repair cells; do not run both versions together.

1. Enable **Internet** and **GPU T4 x2** in notebook settings.
2. Enable the Kaggle secrets `LLAMA_API_KEY` and `NGROK_AUTHTOKEN` for this notebook.
   The API key belongs to the model server; the ngrok token belongs to ngrok.
3. To inspect the current session first, change `CHECK_ONLY = True` and run the cell.
   `Server status: responding` plus generated text confirms inference. `loaded`
   alone is a successful model listing, not proof of inference. GPU memory usage
   alone cannot prove either. The check can consume a few inference tokens but
   does not download files, stop processes, create a tunnel or change settings.
4. Set `CHECK_ONLY = False` and run to repair/start the model. Copy the verified
   HTTPS `API URL` into BugHunter's OpenAI-compatible provider settings, use model
   `bughunter-moe`, and supply the same `LLAMA_API_KEY`.

If the earlier launcher already returned `READY` for Q3 and then failed its
Q4 filename check, paste `scripts/kaggle_connect_running.py` into a **new cell
in that same session**. It reuses the existing launcher helpers, confirms
authenticated inference and opens/verifies ngrok without downloading weights
or restarting the model. A missing ngrok secret is reported separately.
This recovery cell also handles the earlier helper's `Authtoken already set in
configuration file` response without suppressing other token errors. The full
launcher passes the token in its runtime configuration and can reuse an existing
saved ngrok token; it no longer calls `set_auth_token` on every run. It still
requires public authentication and generated text before reporting a verified link.

If no API key is supplied, the launcher generates a private key under
`/kaggle/temp/bughunter-private/llama-api-key`. Read it privately for the client;
it is deliberately not printed into notebook output. It survives cell reruns in
the same session. Kaggle Secrets is preferable across session resets.

## Exact model and recovery

The selected model is **Q3_K_M**, matching the user's existing server, which
returned `READY` on the T4 x2 session. It is reused without downloading Q4 or
restarting the server. The same Q3 file is pinned for a fresh session:

```
Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated.i1-Q3_K_M.gguf
size:   14,711,850,496 bytes
SHA256: 00e4bd529eb749e8eb3397fb9b20d3fedcc63ac82fbed238c7c7ef22c23facd7
```

These values are pinned to the [publisher's file metadata](https://huggingface.co/mradermacher/Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated-i1-GGUF/blob/78b94f476da55d960f88d1830d0993f72f0ee1aa/Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated.i1-Q3_K_M.gguf).
The earlier 13,421,573,148-byte Q4 file was incomplete. The later Q3-vs-Q4
exception was a launcher selection mismatch; the Q3 server was responding.
A GGUF header and a 10 GB minimum do not validate tensor data. The launcher checks the complete hash
before loading any file. Downloads resume in one `.part` file and become final
only after verification. A bad resumed prefix triggers one fresh download of
the same artifact.

An exact verified copy under `/kaggle/input` is reused directly. New downloads
prefer `/kaggle/temp/models`, unless a resumable copy elsewhere has room. Temp
storage is ephemeral and may share the working filesystem; it is not assumed
to provide extra capacity. The space check counts remaining download bytes plus
a 512 MiB reserve. It can reclaim pip caches, old prebuilt runtime archives and
completed llama.cpp build objects when space is insufficient. It preserves
notebooks, other model files, source, executables and shared libraries. It never
automatically kills an existing server to recover memory or disk space.

Existing compatible CUDA binaries are reused. Otherwise the launcher installs
and hashes both archives from the same official [llama.cpp b12098 release](https://github.com/ggml-org/llama.cpp/releases/tag/b12098).
That prebuilt uses CUDA 12.8 and Ubuntu 24.04; an older image without compatible
GLIBC/driver libraries needs a compatible runtime. The launcher reports the
loader error rather than replacing system libraries or falling back to CPU.

## Status and limits

A responding existing process is checked before downloads and is preserved.
A different model, unknown port owner or wrong key gets a specific diagnosis.
Startup failures stop only the process started by that invocation. Missing or
failed ngrok setup leaves a healthy local server running. Rerun to retry.

`/kaggle/working/bughunter-launcher-state.json` records local inference, process
identity, per-process GPU allocations and tunnel verification separately.
`llama-server.log` contains the server log. Both-GPU placement is reported only
when the server's own PID has allocations on both devices; it does not infer
placement from memory used by other notebook processes.

No script can create missing GPU quota, free disk capacity or restore an upstream
outage. Those failures have explicit diagnostics. The notebook has not been run
on the user's live Kaggle GPUs from this workspace.

Local verification:

```sh
python3 -m unittest discover -s tests -p test_kaggle_launcher.py -v
```

The tests exercise real loopback HTTP responses for download interruption,
resume, bad ranges, checksums, authentication and inference. CUDA processes,
disk exhaustion and ngrok are simulated; these tests do not substitute for a
live Kaggle load and generated response.
