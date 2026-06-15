# AGENTS.md — amplifier-bundle-a2a

Agent-to-agent (A2A) communication for Amplifier. The behavior `behaviors/a2a.yaml`
composes a client tool (`tool-a2a`) and an HTTP server hook (`hooks-a2a-server`,
default port 8222), plus a `/a2a` plain-language setup-wizard mode.

## Gates before "done"

1. **Unit + lint** (fast, host). The modules import `amplifier_core`, so a bare
   `pytest` won't collect — run the suite with the deps wired in:

       uv run --no-project --with amplifier-core --with aiohttp --with zeroconf \
         --with pytest --with pytest-asyncio \
         --with-editable modules/tool-a2a --with-editable modules/hooks-a2a-server \
         pytest -q

   Then `ruff format`, `ruff check`, and `pyright` clean on changed code.
2. **Smoke (DTU)** — **required** when you touch the server hook, the behavior,
   module dependencies, startup/config, or the `/a2a` mode. See `SMOKE_TESTS.md`;
   run the saved profile and confirm checks A–D.

See `foundation:docs/PER_REPO_CONVENTIONS.md` for the verification gradient.

## Invariants — do not regress

- **The server is opt-in.** `hooks-a2a-server` stays INERT unless its config has
  `enabled: true`. Never make it start by default — it binds a network port. Gate
  lives in `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`
  (`mount()`).
- **A side-effecting `mount()` MUST return an idempotent cleanup.** Amplifier's
  hook validator calls `mount()` with your REAL config during session init to check
  protocol compliance — so anything `mount()` does with side effects (binding port
  8222, opening a socket) also happens during validation. The validator can only
  undo that via the cleanup you **return** (it cannot drain a Rust-backed
  coordinator's `register_cleanup`-ed functions). Return `None` and the probe-mount
  leaks: the real mount then dies with `[Errno 98] address already in use` /
  `server_running: false`. So `mount()` returns `cleanup` (idempotent) in addition
  to `coordinator.register_cleanup(cleanup)`. See `KNOWN_ISSUES.md`.
- **No silent degrade.** If mDNS is requested but `zeroconf` is missing, fail LOUD
  with a remedy — never skip silently. `zeroconf` is a declared dependency of both
  modules; keep it that way.
- **Sub-agents don't run the server.** The `parent_id` guard plus the behavior's
  `spawn.exclude_hooks` keep the server to the root session only.
- **Identity/contacts are per-user local**, not committed — they belong in a
  project's gitignored `.amplifier/settings.yaml`, never in the bundle.

## Pitfalls

- The modules import `amplifier_core`, so they won't import standalone. Unit-test the
  gate logic with a fake coordinator; use the DTU smoke for the real server path.
- mDNS advertisement fails inside Incus/DTU containers (hostname→IP resolution) —
  that's an environment artifact, not a bug. Verify `zeroconf` *presence*, not LAN
  advertisement, in the DTU.
- **zeroconf must use the ASYNC API.** `advertise_mdns`/`browse_mdns` run inside the
  session's event loop; the synchronous `Zeroconf()` / `register_service()` /
  `ServiceBrowser` raise `zeroconf.EventLoopBlocked` there — and its `str()` is empty,
  so it logs as a blank `mDNS advertisement failed:`. Use `AsyncZeroconf` /
  `async_register_service` / `AsyncServiceBrowser`. Log caught exceptions with `%r`
  (or `exc_info=True`), never `%s` — some zeroconf errors stringify to nothing.
- **Don't fight source overrides to test an unmerged fix.** App-scoped behaviors pin
  their module sources, so `AMPLIFIER_MODULE_*` env vars and `sources:` in
  `settings.yaml` do NOT redirect them to a local checkout (confirmed dead end). To
  exercise a branch in a real session, **commit it and run the DTU smoke** — it mirrors
  your committed branch via Gitea `url_rewrites`. A merged fix only reaches an installed
  CLI after `amplifier bundle update`.

## Done looks like

Unit + lint green; DTU smoke checks A–D PASS with evidence; docs (README / context)
updated if behavior or config changed; PR body filled from
`.github/PULL_REQUEST_TEMPLATE.md`.
