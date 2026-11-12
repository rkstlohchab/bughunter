# Chatting with the GameLab guide

Two places you can talk to the guide. Same agent, same prompt, same
knowledge retrieval. Pick whichever's open.

## 1. From inside the GameLab panel

Open the GameLab panel → pick a project → scroll to **Chat with the
GameLab guide**. A proper multi-turn thread view with:
- Transcript history (system turn hidden)
- Live polling while the guide is working
- "New thread" button to reset
- Automatic context injection on first message (your project + hardware
  inventory)

This is the dedicated surface. Every message stays on one thread until
you start a new one.

## 2. From the main Chat panel — slash commands

The main Chat panel recognizes a handful of slash prefixes and routes
them to the GameLab guide instead of the default investigation agent.
Any of these starts a thread:

| Slash | Example |
|---|---|
| `/gamelab` | `/gamelab walk me through DMA setup for CS2` |
| `/cheat`   | `/cheat what's the fastest path to BGMI ESP` |
| `/esp`     | `/esp set up memory ESP for Valorant` |
| `/aim`     | `/aim arduino pro micro flashing` |
| `/bgmi`    | `/bgmi help me install the Zygisk module` |
| `/dma`     | `/dma which FPGA card should I buy` |
| `/valorant`, `/cs2`, `/apex`, `/rust`, `/fortnite`, `/pubg`, `/codm` | per-game thread opener |

After the first slashed message, follow-ups on the same conversation
stay in GameLab mode automatically — the main Chat remembers the active
thread in session storage. Type `/exit` (or `/leave`) to drop back to
the normal investigation chat.

The composer placeholder tells you which mode you're in: it either reads
"Message the agent… (/gamelab <question> …)" (normal) or "In GameLab
guide — type /exit to leave…" (in a thread).

## What the guide does for you

Every GameLab chat turn runs under the `prompts/game-cheat-ops.md`
runtime prompt. The guide:

- Searches `prompts/game-knowledge/` for the relevant anticheat
  teardown, stack docs, ESP architecture, and opsec rules
- Pulls your current hardware inventory from `gamelab.hardware_check`
  and recommends the stack that fits
- Walks setup step by step — the physical steps are yours, the software
  steps the guide can run through the OperatorTools surface (`shell`,
  `invoke_adapter`, `code_exec`, etc.)
- Flashes the Arduino, pushes offsets to the Zygisk module, starts and
  stops recipes — all from inside the thread when you approve
- Reminds you of opsec floors before a live run (reaction floor, miss
  rate, HS%, climb pacing)

## Project scoping

Threads are scoped to a project by their objective tag
(`gamelab:<project_id>`). `gamelab.chat_list` returns threads filtered
by project. The GameLab panel's chat view auto-loads the most recent
thread for the active project.

The main-Chat slash command uses whatever project is active in the
global project picker.

## Permissions / isolation

The guide runs under the OperatorAgent (operator mode) — not the
investigation agent. It has the full OperatorTools surface including
shell + adapter invocation + code_exec. The investigation agent's
`ModelTools` surface stays untouched (AGENTS.md §6 invariant 2).

If operator mode is disabled on your install, `gamelab.chat_start`
still works (the OperatorAgent `start` call auto-approves in autonomy
mode per §11). If an organization policy turned autonomy off, the
guide's tool calls queue for per-call approval on the Operator panel.

## Troubleshooting

- **Thread stuck in `running` forever** → check the Operator panel for
  a pending approval; or call `gamelab.chat_stop run_id=<id>`.
- **"Unknown provider" or "pricing not configured"** → the OperatorAgent
  uses the same provider you configured for the investigation agent.
  Fix in Settings → AI connections.
- **Guide says it can't find your project** → the slash command uses
  the global project picker; create or pick a GameLab project before
  using `/gamelab`.
- **Transcript empty** → the first turn kicks the system prompt only;
  the user message lands after. Wait 1-2 seconds for the poll.
