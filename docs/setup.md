# Setup and tool connections

## Claude subscription

Install Claude Code independently and authenticate using its own terminal flow:

```sh
claude auth login
claude auth status --json
```

Leave **Settings → Provider** on **Installed Claude Code · subscription**. The default model alias is `opus`. CLI auth detection checks the login method without extracting credentials. An authenticated login is not proof that the subscription can currently make a model call; actual billing/availability errors appear in investigation history. After resolving an error in Claude Code, use **Continue investigation**.

The app passes an isolated workspace, explicit MCP configuration, restricted operation, an empty built-in tool list, and only its allowlisted application tools. User shell, browser, and extra MCP configuration are not part of the model capability surface. Managed organization policies may further constrain operation. A session identifier is saved for CLI resumption.

## Local knowledge tools

Document text imports work without a model. PDF OCR needs local Tesseract; video frames and audio extraction need FFmpeg. Both are discovered from PATH. The macOS desktop also searches `/opt/homebrew/bin`, `/usr/local/bin`, and `~/.local/bin`.

For optional embeddings, install Ollama independently, run it locally, and explicitly download a small embedding model:

```sh
ollama pull nomic-embed-text
```

This command downloads model weights. Set **Local embedding model** to `nomic-embed-text` and the server to `http://127.0.0.1:11434`. Source retry rebuilds an index after adding an embedding model or extractor. Model/digest namespaces prevent mixing embedding spaces. Corrections invalidate stale semantic vectors immediately; keyword results reflect corrections.

For local audio transcription, install `whisper-cli` from [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and download a compatible model using that project's documented workflow. Enter its absolute `.bin` path under **Whisper model path**. On an 8 GB Mac, start with a small/base model and avoid simultaneously running a large local reasoning model. The application runs one ingestion job at a time. It never downloads these model weights itself.

For YouTube, paste a public video URL in **Import resource**. Captions are attempted first. Enable the explicit media-download checkbox to extract local frames/audio where the video is downloadable. No browser cookies, course-account credentials, or arbitrary downloader hooks are loaded. Remote availability and caption language affect results; a failed/partial source can be retried. Matching local SRT/VTT sidecars next to linked media provide timestamped text without speech recognition; you can also import subtitle files directly.

## Burp and the policy gateway

Use a dedicated Burp project/listener for this workbench. Configure the gateway before enabling external execution:

1. Record the target URL(s), authorization, exclusions, limits, and known read-only paths in **Details → Projects**. Use separate session labels for supplied test accounts. Scope is explicit origin plus path prefix; discovering a subdomain does not add it.
2. In **Settings → Advanced tools: Burp, browser, and discovery**, choose the project and **Start gateway**. The default listen address is `127.0.0.1:8091`. The response shows the generated gateway CA path.
3. In the dedicated Burp project, route **all destinations** through the upstream HTTP proxy `127.0.0.1:8091`. Configure Burp to trust the gateway CA for upstream TLS connections. Keep this trust scoped to the dedicated configuration; the app does not change macOS trust settings.
4. Keep Burp's own listener at `127.0.0.1:8080` or enter its actual loopback URL. Turn off interactive interception for managed sends, since the workbench supplies the approval UI. Editing an already approved request in Burp causes a fingerprint mismatch at the gateway.
5. Export Burp's CA certificate as PEM. If Burp exports DER, convert it with `openssl x509 -inform DER -in burp-ca.der -out burp-ca.pem`. Enter the PEM path in Settings.
6. Choose **Verify Burp route**. It checks HTTP and HTTPS probes terminating inside the gateway, using a fresh nonce and a recorded gateway receipt. Verification lasts five minutes. Verify again after restarting or changing the proxy configuration.
7. Execute only managed requests. Do not use this listener for unrelated browsing: requests without application tickets are rejected.

For history and Repeater handoff, install the [official MCP Server extension](https://portswigger.net/bappstore/9952290f04ed4f628e624d0aa9dccebc), enable its loopback SSE endpoint, and allow scoped history access. The default application endpoint is `http://127.0.0.1:9876/sse`. The integration negotiates available tools and detects optional scanner capability. This version does not run Burp Scanner or Collaborator. Repeater handoff creates a tab without sending it; a manual send without a managed request ticket is blocked by the dedicated gateway.

Live Burp verification requires an installed/running Burp instance. The automated suite uses real loopback gateway traffic and Chromium, not an imitation claim of Burp verification.

## Optional tools and provider modes

Install Subfinder and Nuclei separately from their official projects; the application discovers them from PATH. Missing binaries remain unavailable. Nuclei currently runs one application-owned HTTP metadata template, with redirects, retries, updates, and out-of-band checks disabled. These results do not confirm a finding.

The managed Playwright browser needs the Chromium version matching the Python Playwright dependency. `scripts/setup.py` installs it. The browser is headless and records screenshots; use its bounded workflow tools through an investigation. Browser state is stored separately for each project/session. Its writes wait for **Approve exact request → Send once**; leaving them pending for 150 seconds cancels the flow. Background requests and redirects are checked individually.

For billed providers, select the mode explicitly, enter the model and optional endpoint, save the API key in Keychain, enter checked input/output prices, confirm them, and save. Costs are estimates from configured prices and provider usage, with conservative reservation before each request. They are not a provider invoice or a substitute for provider-side account spending limits.

For local reasoning, select Ollama and a locally stored model advertising tool support. Enable **Local-only inference**. Cloud-backed models and remote Ollama addresses are then rejected. Source URL downloads are explicit imports and remain separate from model inference.

Community "abliterated"/uncensored fine-tunes of open models run through the same Ollama path (`ollama pull <model>` locally, then select that model). The application's guardrails never depend on model refusals — scope, gateway, and approvals sit outside the model — so an unfiltered local brain changes drafting behavior, not what can reach a target.

### Remote Ollama server (Kaggle notebook or rented GPU)

Select **Remote Ollama · your own GPU server** to reason on a GPU box you control, reached over an authenticated HTTPS tunnel. It is deliberately treated like a hosted provider: it requires a stored access key, the endpoint must be HTTPS (plain HTTP is accepted only for loopback testing), local-only mode blocks it, and your embedding index never leaves this machine.

One notebook, run when you need it:

```python
# Cell 1 — Ollama on loopback + a token gate. Nothing passes without the exact key.
import http.server, os, subprocess, threading, urllib.request, urllib.error
os.environ["OLLAMA_HOST"] = "127.0.0.1:11434"
subprocess.Popen(["ollama", "serve"])
subprocess.run(["ollama", "pull", "hf.co/<user>/<repo>:Q4_K_M"])  # your chosen GGUF
TOKEN = "pick-a-long-random-string"
class Gate(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _forward(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        data = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or None
        req = urllib.request.Request("http://127.0.0.1:11434" + self.path, data=data, method=self.command)
        try:
            up = urllib.request.urlopen(req, timeout=600); payload, status = up.read(), up.status
        except urllib.error.HTTPError as e:
            payload, status = e.read(), e.code
        self.send_response(status); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    do_GET = do_POST = _forward
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", 8787), Gate).serve_forever, daemon=True).start()
```

```bash
# Cell 2 — quick tunnel; it prints the https://…trycloudflare.com URL to paste into Settings.
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared
(./cloudflared tunnel --url http://127.0.0.1:8787 > tunnel.log 2>&1 &) && sleep 6 && grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1
```

In the app: paste the tunnel URL as the API base URL, enter the pulled model id, store `TOKEN` as the API key, set both price fields to 0 (the GPU is yours) and tick the pricing confirmation, then **Save & test connection**. Practical notes: a quick-tunnel URL changes every session, so re-paste it each time; Kaggle sessions are time-limited and a dying session fails the run in flight (resume after restart); tunnels from notebooks sit in a gray zone of Kaggle's ToS — verify before relying on this, or use a named tunnel on your own domain; and a 27B-class Q4 model on a T4 pair is slow, so raise the run time limit in Settings for long study runs.

### DeepSeek API (hosted)

Select **DeepSeek API · chat / reasoner**, create a key at platform.deepseek.com, and store it in the API key field. The base URL defaults to `https://api.deepseek.com` and can stay empty. Two model ids: `deepseek-chat` is the tool-calling model the agent loop uses natively; `deepseek-reasoner` trades native tools for longer visible chains of reasoning, so the app routes it through the same prompt-level tool protocol used for CrucibleLLM instead of sending the tools parameter. Enter the current per-million input/output rates from DeepSeek's pricing page in both price fields and tick the confirmation — the budget guard then estimates every call before it is sent. **Save & test connection** uses the free `/models` listing to verify the key without a billed request, and flags a model id that is not listed. Local-only mode blocks this provider; the scope, policy gateway, and approvals apply exactly as with any provider.

## Packaging

`python3 scripts/package.py` freezes Python into a Tauri sidecar for the host architecture, then builds the app bundle. Apple Silicon builds use `aarch64-apple-darwin`. The source build needs Xcode command-line tools and Rust. The packaged personal app is ad-hoc signed and not notarized; public redistribution/signing is separate work. Quit the app before replacing the bundle. Reopening retains sources and evidence and pauses interrupted investigations.
