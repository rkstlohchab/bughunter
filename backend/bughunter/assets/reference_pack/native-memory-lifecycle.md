# Native memory acquisition and lifetime discipline

Authored synthesis and repository observations; reviewed 2026-11-11. No game-specific layouts or offsets are asserted.

## Read and write contract

On Windows, `ReadProcessMemory` needs a process handle with read access and reports the transferred byte count. Reads that cross inaccessible regions fail. Treat a partial structure as missing evidence instead of decoding uninitialized bytes. [Microsoft ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory).

`WriteProcessMemory` requires appropriate process access and an accessible destination range. Check the result and transferred size. A failed write does not justify silently changing page protections or broadening the operation. [Microsoft WriteProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-writeprocessmemory).

## Method: make session identity explicit

A PID is not a complete lifetime token. Bind pending work to the current session object and retire it on detach or replacement. Stop repeated writers before releasing the underlying handle or task port. Serialize lifetime changes with memory operations. A freeze operation needs a successful initial write, a stop mechanism, and a terminal failure path; it must never continue against a retired session merely because the numeric PID matches.

## Worked repository correction

The native macOS backend serializes access with a reentrant lock, retires session freezes, deallocates task and region object ports, and refuses a failed write without a protection-changing retry. The freeze worker checks that the stored session is still the exact attached session before each write. Fake-Mach regression tests exercise these transitions without claiming real target access.

## Validation record

For a real run record OS build, architecture, process identity, attach result, address range, region permissions, requested bytes, actual bytes, and cleanup outcome. Keep failures explicit. Mock lifecycle tests do not establish entitlement availability, game compatibility, read performance, or undetectability. Use the existing operator-driven local bridge; this note grants no new execution interface.
