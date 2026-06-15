# Running A2A on WSL2 (Windows)

This guide explains how to make the A2A bundle reachable on your **local network**
when Amplifier runs inside **WSL2** on Windows. It is the result of getting LAN
discovery and inbound connections working end-to-end on a real WSL2 host.

If you only ever connect to agents over **Tailscale/VPN** or the **internet**, you
can skip most of this — those paths use manual `known_agents` URLs and do not
require the networking change below. This guide is specifically about the
**Same LAN + mDNS auto-discovery** scenario.

## TL;DR

1. WSL2's default **NAT** networking hides your distro behind a private virtual
   network, so peers on your LAN cannot reach the A2A server and mDNS/multicast
   does not cross the boundary. Switch WSL2 to **mirrored networking**.
2. Install **`zeroconf`** — it is an *optional* runtime dependency, and without it
   mDNS advertisement/discovery is silently disabled.
3. Open the A2A **port (default 8222)** in Windows Firewall for inbound connections.
4. Set **`base_url`** to the machine's real LAN IP so the agent card advertises a
   reachable address (the WSL hostname does not resolve to a LAN-reachable IP).

---

## 1. Why the default doesn't work

Out of the box, WSL2 uses **NAT** networking. Your Linux distro gets a private IP
on a virtual switch (typically `172.x.x.x`), not an address on your real LAN.
Consequences for A2A:

- **Inbound is blocked** — other devices on your LAN cannot open a TCP connection
  to the A2A HTTP server running inside WSL2.
- **mDNS doesn't cross the NAT** — multicast discovery (`_a2a._tcp.local.`) does
  not traverse the virtual switch, so `a2a discover` finds nothing and peers
  can't find you either.

The fix is **mirrored networking mode**, where WSL2 shares the Windows host's
network interfaces. Your distro then appears on the LAN with the host's IP,
inbound connections work, and multicast/mDNS reaches the LAN.

## 2. Requirements

- **Windows 11, version 22H2 or later** (mirrored networking is not available on
  older builds). Verified on Windows 11 25H2 (build 26200).
- Ability to edit `C:\Users\<you>\.wslconfig` and run an elevated command once.

## 3. Enable mirrored networking

Create or edit **`C:\Users\<you>\.wslconfig`** (Windows-side path, not inside WSL):

```ini
[wsl2]
# Share the Windows host's network interfaces. The distro appears on the LAN
# with the host IP; replaces NAT and enables inbound + mDNS/multicast.
networkingMode=mirrored

[experimental]
# Let Windows <-> WSL talk over localhost (e.g. a web UI on :4000, A2A on :8222).
hostAddressLoopback=true
# Help DNS + multicast/mDNS behave across the mirror.
dnsTunneling=true
autoProxy=true
```

Apply it by fully restarting WSL from a Windows terminal (PowerShell/cmd):

```powershell
wsl --shutdown
```

> **Caution:** `wsl --shutdown` terminates **every** WSL process — all running
> sessions, servers, and background jobs. Save your work first. The change also
> takes effect on the next WSL start regardless (e.g. after a reboot), so once
> `.wslconfig` is in place the switch is "armed."

After WSL restarts, confirm your distro now has a real LAN address (and the old
`172.x` NAT address is gone):

```bash
ip -brief addr        # expect your LAN IP, e.g. 192.168.1.x
hostname -I
```

## 4. Install zeroconf (required for mDNS)

mDNS support depends on the `zeroconf` package, which is an **optional** runtime
dependency. If it is not installed, the server logs that mDNS is skipped and
discovery silently does nothing.

Install it into the **same Python environment Amplifier runs in**. If you installed
Amplifier with `uv tool install`, that is the tool venv:

```bash
uv pip install --python ~/.local/share/uv/tools/amplifier/bin/python zeroconf
```

Or, if you maintain the modules in their own extras, install the `mdns` extra:

```bash
pip install "amplifier-module-hooks-a2a-server[mdns]"
pip install "amplifier-module-tool-a2a[mdns]"
```

Verify:

```bash
python -c "import zeroconf; print('zeroconf', zeroconf.__version__)"
```

## 5. Open the firewall port

Mirrored networking lets inbound connections reach WSL, but Windows Firewall must
allow the A2A port. Run **once** in an elevated PowerShell/cmd (default port 8222):

```powershell
netsh advfirewall firewall add rule name="Amplifier A2A inbound 8222" ^
  dir=in action=allow protocol=TCP localport=8222
```

(Adjust the port if you changed `hooks-a2a-server.config.port`.)

## 6. Advertise a reachable address with `base_url`

Inside WSL, `socket.gethostname()` typically resolves to the Windows machine name,
which is **not** a LAN-reachable IP. Set `base_url` explicitly to your LAN IP so
the agent card (`/.well-known/agent.json`) and mDNS record advertise an address
peers can actually connect to:

```yaml
hooks:
  - module: hooks-a2a-server
    config:
      port: 8222
      base_url: "http://192.168.1.154:8222"   # your machine's real LAN IP
      discovery:
        mdns: true
```

## 7. Verify end-to-end

With a session running the A2A bundle:

```bash
# 1. The agent card is reachable on the LAN (run from WSL or another device):
curl -s http://192.168.1.154:8222/.well-known/agent.json

# 2. mDNS advertises the service — browse for it:
python - <<'PY'
import socket, time
from zeroconf import Zeroconf, ServiceBrowser
found = {}
class L:
    def add_service(self, zc, t, n):
        i = zc.get_service_info(t, n, timeout=1500)
        if i:
            found[n] = ([socket.inet_ntoa(a) for a in i.addresses], i.port)
    def update_service(self, *a): pass
    def remove_service(self, *a): pass
zc = Zeroconf(); ServiceBrowser(zc, "_a2a._tcp.local.", L())
for _ in range(15):
    if found: break
    time.sleep(1)
print("Discovered:", found or "NONE")
zc.close()
PY
```

A reachable agent card plus a non-empty mDNS discovery result confirms the LAN
path is working.

## Notes on Tailscale

Tailscale running **inside** WSL continues to work in mirrored mode in practice —
you end up with both a LAN path (via the mirrored host interface) and your tailnet
addresses. If you prefer, you can instead run Tailscale only on the Windows host
and let WSL inherit it through the mirror. Either way, remote peers still connect
via manual `known_agents` URLs using the Tailscale address.

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `ip addr` still shows only `172.x` | `.wslconfig` not applied — run `wsl --shutdown` and reopen; confirm the file is at `C:\Users\<you>\.wslconfig`. |
| Peers can't reach the agent card | Firewall rule missing — add the inbound TCP rule for your port. |
| `a2a discover` / browse finds nothing | `zeroconf` not installed in Amplifier's Python env, or `discovery.mdns` is false. |
| `mDNS advertisement failed:` with an empty error | Multi-interface host where the service record had no address — ensure `base_url` is set to your LAN IP (recent versions derive the mDNS address from it). |
| Agent card `url` is a hostname peers can't resolve | Set `base_url` to the LAN IP explicitly. |
| `port 8222 already in use` | Another session/process holds the port — stop it, or set a different `port` in `hooks-a2a-server` config. |
