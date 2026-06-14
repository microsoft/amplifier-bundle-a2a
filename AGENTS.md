# AGENTS.md — amplifier-bundle-a2a

Agent-to-agent (A2A) communication for Amplifier. The behavior `behaviors/a2a.yaml`
composes a client tool (`tool-a2a`) and an HTTP server hook (`hooks-a2a-server`,
default port 8222), plus a `/a2a` plain-language setup-wizard mode.

## Gates before "done"

1. **Unit + lint** (fast, host): `pytest -v` at the repo root; `ruff format`,
   `ruff check`, and `pyright` clean on changed code.
2. **Smoke (DTU)** — **required** when you touch the server hook, the behavior,
   module dependencies, startup/config, or the `/a2a` mode. See `SMOKE_TESTS.md`;
   run the saved profile and confirm checks A–D.

See `foundation:docs/PER_REPO_CONVENTIONS.md` for the verification gradient.

## Invariants — do not regress

- **The server is opt-in.** `hooks-a2a-server` stays INERT unless its config has
  `enabled: true`. Never make it start by default — it binds a network port. Gate
  lives in `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`
  (`mount()`).
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

## Done looks like

Unit + lint green; DTU smoke checks A–D PASS with evidence; docs (README / context)
updated if behavior or config changed; PR body filled from
`.github/PULL_REQUEST_TEMPLATE.md`.
