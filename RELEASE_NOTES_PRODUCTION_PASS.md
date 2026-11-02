# Production hardening pass

This pass improves archive review, coding-agent lifecycle, and build checks.
It does **not** claim production readiness.

**New**: AI Testing can inspect an existing imported ZIP of jailbreak/prompt
robustness examples. Its pattern matches are candidates, linked to the
original source member, not validated techniques or attacks. A curated,
versioned descriptive taxonomy is included in the Knowledge Library.

**Hardened**: coding CLI runs now have a bounded output line reader and a
15-minute watchdog, with a failure record for timeouts. The runner also
strips credential-named environment variables and attempts to reap/kill process
groups on exit. That is not a substitute for OS-level isolation.

**Added**: GitHub Actions checks for Python on macOS/Linux and frontend builds.

**Still required**: live Mac/Windows sidecar builds, actual running CLIs,
end-to-end model traffic tests, backup/restore/migration tests and process
isolation/security verification. See `PRODUCTION_REVIEW.md`.
