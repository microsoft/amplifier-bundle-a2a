# Running on WSL2 — reaching the a2a server from another machine

## Why WSL needs special setup

By default WSL2 uses NAT networking. Connections from machines on your LAN can't
reach the WSL VM, and multicast/mDNS (`_a2a._tcp`) doesn't cross the NAT boundary.
So out of the box, port 8222 is reachable from your Windows host but not from a
peer's machine, and `.local` discovery won't work for them.

Two things to fix: make the port reachable from outside, and give peers an address
they can actually resolve.

---

## Fix A — mirrored networking (recommended)

> Requires Windows 11 22H2 or newer. On Windows 10 or older, skip to Fix B.

On the **Windows** side, edit (or create) `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then from a Windows terminal:

```
wsl --shutdown
```

Reopen WSL. In mirrored mode the VM shares the Windows host's network interfaces
directly — inbound connections land in WSL and mDNS traffic crosses normally, so
LAN discovery works without any extra config.

---

## Fix B — port proxy (fallback for NAT mode / older Windows)

If mirrored networking isn't available, forward the port from Windows into WSL.

First, get your WSL IP from inside WSL:

```bash
hostname -I
```

Then from an **admin** Windows terminal:

```
netsh interface portproxy add v4tov4 listenport=8222 connectaddress=<wsl-ip> connectport=8222
```

This must be re-run whenever WSL gets a new IP (every WSL restart reassigns it).

**Note:** mDNS advertisement still doesn't cross the NAT boundary in this mode.
Peers must reach you by explicit address — see "A reachable address for peers" below.

---

## Windows Firewall

When connections originate from another machine, Windows Firewall blocks them by
default even with Fix A or B in place.

From an **admin** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Amplifier a2a 8222" -Direction Inbound -LocalPort 8222 -Protocol TCP -Action Allow
```

---

## A reachable address for peers

**Mirrored networking:** peers can use your `.local` hostname. Verify it resolves
from inside WSL first:

```bash
getent hosts $(hostname).local
```

If that returns an IP, `http://$(hostname).local:8222` is what to share.

**NAT / port-proxy mode:** `.local` won't resolve for peers. Set an explicit
`base_url` in the project's `.amplifier/settings.yaml` pointing at your Windows
host's LAN IP:

```yaml
overrides:
  hooks-a2a-server:
    config:
      enabled: true
      base_url: "http://192.168.1.50:8222"
```

The server uses `base_url` as the address it advertises in its Agent Card. Without
it, the card advertises the internal WSL address, which peers can't reach.

If your router assigns IPs via DHCP, this address can change. A reserved or static
DHCP lease avoids that.

---

## Verify

From inside WSL — confirms the server is up:

```bash
curl -s http://127.0.0.1:8222/.well-known/agent.json
```

From another machine on the LAN — confirms it's reachable:

```bash
curl http://<address>:8222/.well-known/agent.json
```

Both should return your Agent Card JSON.

If you're using mirrored networking with mDNS enabled, you can also check
advertisement from any Linux machine on the LAN (requires `avahi-utils`):

```bash
avahi-browse -rtp _a2a._tcp
```

---

## Tailscale coexistence

Tailscale gives you a stable cross-network address that sidesteps LAN/NAT entirely.
Set `base_url` to your Tailscale hostname or IP, and add your peer's Tailscale
address under `known_agents`:

```yaml
overrides:
  hooks-a2a-server:
    config:
      enabled: true
      base_url: "http://100.64.0.5:8222"
      known_agents:
        - name: "Friend's Agent"
          url: "http://100.64.0.12:8222"
```

This works alongside mirrored networking — Tailscale handles cross-network peers,
mDNS handles local LAN peers.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Peer gets connection refused | Firewall blocking inbound | Add the firewall rule above |
| Port proxy mode: peer gets connection refused | WSL IP changed | Re-run `netsh portproxy` with the current IP |
| `.local` doesn't resolve for peer | NAT mode active | Set `base_url` to the Windows host's LAN IP |
| Nothing on `avahi-browse` | NAT mode — mDNS can't cross | Expected; use explicit addresses instead |
| Works from localhost, not remotely | Firewall or `base_url` not set | Check the firewall rule; verify `base_url` in settings |

---

This guide is loaded automatically by the `/a2a` expert mode when it detects WSL.
