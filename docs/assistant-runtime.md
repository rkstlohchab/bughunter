# Assistant runtime

Chat now exposes one **Agent settings** button beside the model and effort controls. It groups Plan / Auto / Full access, Background / Foreground, permissions, and Auto / Always / Off web search. Settings can apply to all chats, one chat, or one model. Saving access configures future tasks; running tasks retain their original grants. A new chat scoped setting can create an empty conversation before its first message. Stop all cancels work and does not replay it on Resume.

## Background work

The harness exposes `delegate_task` and `worker_status` to all configured providers through the existing tool protocol. Two delegated workers execute concurrently; a parent can queue three bounded subtasks. They inherit the parent's model, project, stop generation and reduced permissions. Recursive delegation is blocked. The parent collects completed results without spending model turns polling and produces the combined reply. Local Ollama inference is serialized.

New chat and switching chats leave server jobs running. The separate Tasks panel lists task state, lets the user open or stop work, and holds execution receipts. The conversation has no tool log, completion card, Task access button or Ready for your reply banner.

Background desktop work uses the Vercel engine's strict headless semantics. Foreground keyboard/mouse commands and window activation are blocked in background mode. App actions serialize per target. Some apps require focus and cannot be fully automated in the user's background desktop; the agent must pause and ask to switch rather than take over input. Separate OS desktops/VMs are not provisioned by this implementation.

The controller overlay size is 0.65 (previously 1.3). Background overlays are hidden unless their target app/window is focused. Window IDs distinguish two windows of the same app where available. Stop and session cleanup remove the overlay. OS-specific rendering and interaction still require the desktop engine's permissions.

## Shared conversation memory

`ConversationMemory` stores labeled, cited recaps and explicit notes independently of the model provider. It backfills up to 200 existing chats, uses local FTS retrieval, includes current conversation context, and keeps project-specific recall inside that project. Small local models receive bounded history, shorter tool receipts, and separately labeled memory. The stored transcript remains intact.

Settings provides enable/disable, search, preference/correction notes and Forget. Forgetting a recap prevents its automatic regeneration. Deleting a conversation removes its cited memory and unneeded reference files. A recap is quoted context, not verified truth or a tool permission. Relevant retrieval is not a guarantee that every historical detail fits every model. No weights are trained by this feature.

## Plugins

Settings → Plugins offers GitHub and Vercel presets and custom MCP connections. HTTP with bearer tokens, OAuth browser sign-in, no authentication, and local stdio servers are supported. Stdio arguments are separate argv elements; the app does not construct a shell command. Header, environment and OAuth secrets use the system credential store and are excluded from model-visible plugin lists.

GitHub uses its official `https://api.githubcopilot.com/mcp/` endpoint with an operator-supplied personal access token. Vercel uses `https://mcp.vercel.com` with browser OAuth. Public Vercel metadata advertised dynamic registration, PKCE S256 and an authorization server at `https://vercel.com` during verification. Account authentication and account-scoped live tools were not exercised: each user must connect their account in the UI. Do not label a preset as connected merely because it appears in Settings.

The host discovers paginated tool schemas. Models use `plugin_list`, `plugin_tools` and `plugin_call`; adding connections, credentials, disabling and removing plugins are desktop settings operations. Plan mode and ordinary read-only integration use require an explicit read-only annotation and reject destructive tools. Full/Auto tasks need the Connected plugins grant for account actions. Disabling a connection or Stop all cancels in-flight work. New models can reuse these integrations without provider-specific plugin plumbing. Legacy SSE-only servers and provider-specific non-MCP plugin formats need an adapter.

## Chat references and images

Chat uploads go to `<data>/chat-references`, never `sources.import`, Library indexing or training. Up to six files under 64 MB each can be attached. Text/code, PDFs, office XML text and archive manifests have bounded previews with coverage notes. Unknown binary formats retain metadata and a local reference path for permitted analysis tools; the app does not pretend to understand their contents. Large documents and scanned PDFs have explicit extraction limits. Uploaded code, macros and archives are never executed by previewing them.

Images and scanned pages use the selected model's image transport or an explicitly chosen local/cloud vision assistant. Ollama image capability is checked through its model metadata. Unverified/text-only connections show a warning and a shortcut to Video AI setup; the Send button requires a vision choice for an image. The vision assistant produces labeled observations for the current model, with uncertainty and filename citations. Cloud image sharing and local-only settings are enforced. Successful image analysis is cached for the same reference, question and model configuration; failures are not cached. This is a vision-to-text relay, not adding vision weights to a text model.

## Teaching and speech

Markdown, code, math and highlights remain available. Fenced `mermaid` blocks now render as diagrams with source view and SVG download. Rendering uses Mermaid strict mode, no HTML labels, and bounded diagrams; generated SVG is displayed as an image. Arbitrary on-screen annotation over other apps is not implemented.

Voice input uses `moonshine-voice==0.1.6` in a separate managed runtime. Tiny Streaming and Small Streaming English models are installed on this Mac, with Tiny selected. Settings can install, select and cancel setup. Model downloads reuse the SDK cache and require 1 GiB free. A successful local streaming smoke test is required for Ready.

The microphone starts only through the visible Chat control. WebAudio feeds 16 kHz mono PCM to a persistent local worker; audio is not saved. Partial speech replaces the current utterance directly in the composer; completed lines are committed without duplication. Common filler cleanup and spoken commands are enabled automatically. Say “send”, “send it” or “send a message” as a separate utterance to submit the draft, without a wake phrase. Ordinary sentences that mention sending stay as dictated text. Listening continues after submission until Stop listening, switching conversations, leaving Chat or Stop all closes the stream and worker. Manual edits are retained and delayed results cannot reinsert a sent draft. macOS microphone consent is requested when listening is turned on.

A 9.444 second Samantha synthetic English sample, including “um” and a 1.6 second mid-sentence pause, measured Tiny at 263 ms / 221.1 MiB peak RSS and Small at 633 ms / 490.9 MiB. Both had 5% word error against the original transcript, involving the filler. These are one synthetic fixture's measurements, not an accent benchmark. Real streaming produced partial/final callbacks and stopped without retaining audio. Current wheel availability includes macOS 15+ ARM, Windows x64 and supported Linux targets; native behavior outside this Mac was not observed.

## App installation and web search

The existing dependency installer now searches Homebrew formulae/casks, winget, apt, dnf and pacman catalogs. Exact search results become validated package IDs with argv-only plans. Task installations require the Install grant, retain output/status and respond to cancellation. Account and OS administrator prompts still apply. A successful package-manager receipt is distinguished from executable detection. No arbitrary remote script is treated as a trusted installer by this search path.

Auto web search gives the model a public-source search tool and prefetches for explicit/current-information requests in ordinary chat. Always requests public source search; Off blocks the host search tools. Search result URLs are the citations; excerpts are unverified evidence, not instructions. Search requests do not automatically enter Library.

## Verification and rollback

Verification files, source baselines, modified copies, hashes, the patch and rollback helper are under `artifacts/assistant-runtime-2026-11-13/`. The database backups there are private and must not be committed or shared. The preexisting course checkpoints were preserved; both courses continue from their checkpoints when the selected provider has capacity, with automatic cooldown when it reports a limit. Extraction has not completed and no training was started.

Use `backend/.venv/bin/python artifacts/assistant-runtime-2026-11-13/rollback.py --check` before restoring. `--apply` restores only this work's source files after verifying their modified hashes. It refuses to overwrite later changes. Then run `npm ci` and rebuild the desktop. This does not roll back course progress, delete downloaded speech models or overwrite the live database. Disable or forget conversation memory through Settings if desired. Database restore is a separate operator decision because it would discard later work. A reconstruction test compares every restored file with its recorded baseline hash.

Library now shows Pause / Continue beside each file and in source details. Partial resources display their specific warning. Continue preserves completed media checkpoints; a known provider cooldown remains in effect. The saved frame count stays visible while the scanner rechecks older cache entries. Image-page PDF supplements are tied to the original source hash, cited as model interpretations, and reused on retry. Day One PDF page 6 now has a GPT-6.1 Sol interpretation; its tiny unreadable labels remain an explicit partial-extraction warning.
