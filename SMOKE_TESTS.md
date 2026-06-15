# Smoke Tests — amplifier-bundle-a2a

Unit tests prove the code does what it was written to do. These smokes prove the
bundle actually **composes, installs, and runs** inside a real Amplifier environment.

**Run them when a change touches** the server hook, the behavior, module
dependencies, startup/config, or the `/a2a` setup mode. (Pure docs/test-only changes
don't need a smoke.)

## Runnable: saved DTU profile

A Digital Twin Universe profile stands up a realistic Amplifier install with this
bundle app-composed:

    .amplifier/digital-twin-universe/profiles/a2a-smoke.yaml

Launch it (validates a local working-tree mirror via Gitea):

    amplifier-digital-twin launch \
      .amplifier/digital-twin-universe/profiles/a2a-smoke.yaml \
      --var GITEA_URL=http://<host-ip>:<port> --var GITEA_TOKEN=<token> \
      --name a2a-smoke

The mirror reflects your **committed** branch state — `git commit` your fix before
launching, or the DTU will test stale code. This is the supported way to exercise an
unmerged branch in a real session (source overrides do NOT redirect app-scoped
modules; see `AGENTS.md` pitfalls).

Drop the `url_rewrites` + `--var` flags (see the profile's header comment) to
validate the **published** bundle from GitHub instead of a local mirror.

## Acceptance checks

| # | Check | Pass = |
|---|-------|--------|
| A | App-install composes; `zeroconf` auto-present | `amplifier bundle list` shows `a2a` (app); `zeroconf` in the amplifier env |
| B | `/a2a` mode is discoverable | a session's `mode(list)` includes `a2a` after the behavior is app-composed |
| C | **Opt-in gate OFF** | a dir with no a2a config → port 8222 **never binds** (server stays inert) |
| D | **Opt-in gate ON** | a dir whose `.amplifier/settings.yaml` sets `hooks-a2a-server.config.enabled: true` → 8222 listening; `GET /.well-known/agent.json` → HTTP 200 |
| E | (stretch) two agents exchange a message | needs two reachable, mutually-configured agents — best-effort, not required |

A–D are the **required** gates for any change to the hook / behavior / deps / mode.
E is best-effort (in-container mDNS advertisement is unreliable and isn't part of
most changes).

## Last verified

2026-06-15, branch `fix/validator-double-mount` (DTU instance `a2a-smoke`):
A–D **PASS**, E N/A. `zeroconf 0.149.16` auto-installed; gate ON → 8222 bound at ~52s
and `GET /.well-known/agent.json` → HTTP 200; gate OFF → 8222 stayed inert. This run
also confirmed the hook-validator double-mount fix (see `KNOWN_ISSUES.md`).
