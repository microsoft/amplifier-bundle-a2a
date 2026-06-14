## What & why

<!-- One or two lines: what this changes and why. -->

## Verification

Fill each box with **real evidence**, `N/A — <reason>`, or stop and flag it if it
applies but you can't honestly satisfy it. See `AGENTS.md` and `SMOKE_TESTS.md`.

- [ ] **Unit + lint**: `pytest -v` passes; `ruff format` / `ruff check` / `pyright` clean on changed code
- [ ] **Smoke (DTU)** — required if this touches the server hook, behavior, module deps, startup/config, or the `/a2a` mode. Ran `.amplifier/digital-twin-universe/profiles/a2a-smoke.yaml`; paste results:
  - A — app-install composes + `zeroconf` present:
  - B — `/a2a` mode discoverable:
  - C — gate OFF → port 8222 inert:
  - D — gate ON → agent card HTTP 200:
- [ ] **Invariants held** (see `AGENTS.md`): server stays opt-in (`enabled`-gated, no default-on); mDNS-missing fails loud, not silent; `zeroconf` still a declared dep of both modules; sub-agents don't run the server
- [ ] **Docs updated** (README / context) if behavior or config changed

## Notes / follow-ups

<!-- Anything deliberately deferred? Offer it for KNOWN_ISSUES.md and confirm before adding. -->
