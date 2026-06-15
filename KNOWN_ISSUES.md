# Known Issues & Hard-Won Lessons — amplifier-bundle-a2a

Resolved bugs kept here so the same classes don't cost a debug session twice.
Each has a fast, decisive re-verify that does NOT need a full CLI run.

## RESOLVED — hook-validator double-mount left port 8222 stuck (PR #7, `706f73f`)

**Symptom:** on a real host the enabled server logs
`A2A server failed to start: port 8222 is already in use [Errno 98]` and `whoami`
reports `server_running: false` — yet 8222 is free before and after the session.

**Cause:** Amplifier's hook validator
(`amplifier_core/validation/hook.py::_check_protocol_compliance`) calls `mount()`
with the REAL config to check protocol compliance — which binds 8222 — then fails
to run the cleanup, because the probe uses a Rust-backed `MockCoordinator` whose
`register_cleanup`-ed functions it cannot drain. The leaked probe-server holds the
port; the real mount collides.

**Fix:** `mount()` returns an idempotent cleanup. The validator invokes the
*returned* cleanup (`hook.py:380 await mount_result()`), freeing the port before the
real mount runs. (See the `mount()` invariant in `AGENTS.md`.)

**Upstream root fix (separate, recommended):** amplifier-core should drain a
coordinator's registered cleanups regardless of backend. This bundle's fix is
defense-in-depth so a2a is robust even on a kernel that still has the bug.

**Note:** the DTU smoke does NOT reproduce this (it's host/validator-specific — the
container's gate-ON check passed even with the bug present). The decisive proof is
the harness below.

## RESOLVED — mDNS "advertisement failed:" with an empty error (PR #7, `ae0e469`)

**Symptom:** mDNS never advertises; the log shows `mDNS advertisement failed: ` with
nothing after it.

**Cause:** synchronous `zeroconf.register_service()` / `ServiceBrowser` called inside
the running asyncio loop raise `zeroconf.EventLoopBlocked`, whose `str()` is empty
(hence the blank log line).

**Fix:** use `AsyncZeroconf` / `async_register_service` / `AsyncServiceBrowser`; log
caught exceptions with `%r` + `exc_info`, never `%s`.

## Re-verify either fix in ~30s (no CLI, no DTU)

Run against the module source with the deps wired in
(`uv run --no-project --with amplifier-core --with aiohttp --with zeroconf python - <<'PY' ...`):

- **Double-mount:** drive the real validator and assert the port is released —
  `HookValidator()._check_protocol_compliance(ValidationResult(module_type="hook",
  module_path="x"), mount, {"enabled": True, "host": "127.0.0.1", "port": 8222,
  "agent_name": "v", "discovery": {"mdns": False}})`, then check 8222 is FREE.
  With the fix it's free; wrap `mount` to discard its return (simulating the old
  bug) and the port stays stuck.
- **mDNS:** `await advertise_mdns("v", 8222, "http://127.0.0.1:8222")` from inside
  `asyncio.run(...)` and assert it returns a non-None handle (no `EventLoopBlocked`).
