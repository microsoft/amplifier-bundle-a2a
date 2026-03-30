# amplifier-bundle-a2a

Agent-to-Agent communication for [Amplifier](https://github.com/microsoft/amplifier) via Google's [A2A protocol](https://github.com/google/A2A).

Enables two Amplifier sessions running on different devices — potentially different apps (CLI, web, native) — to discover each other, exchange messages, and collaborate over a network.

## What It Does

Two people, each running their own Amplifier session, can talk to each other's agents:

```
Ben's session                              Sarah's session
    |                                           |
    |  "Ask Sarah what restaurant tonight"      |
    |  ──────────────────────────────────────>  |
    |                                           |  (Sarah sees the message
    |                                           |   on her next turn)
    |                                           |
    |                                           |  "Sushi Nozawa!"
    |  <──────────────────────────────────────  |
    |                                           |
    |  "Sarah says Sushi Nozawa"                |
```

All communication happens over HTTP using the A2A protocol. The bundle handles discovery, trust, message routing, and response delivery.

## Features

### Three Interaction Modes

| Mode | When | What Happens |
|------|------|-------------|
| **Mode C** (Autonomous) | Trusted contacts | Agent answers automatically using available tools and context |
| **Mode A** (Notify-and-wait) | Known contacts | Message queued for the user; they respond on their own time |
| **Mode B** (Live injection) | Active session | Message appears immediately in the user's current session |

Modes escalate automatically: C (can't answer?) -> A (user is active?) -> B. Users can defer Mode B messages back to Mode A with "not now."

### Discovery

- **mDNS/Zeroconf** — Agents on the same LAN find each other automatically
- **Manual URLs** — For Tailscale, VPN, or cross-network connections

### Trust & Contacts

- **Unknown agents** are blocked until the user approves them
- **Known contacts** require the user's input for every message (Mode A)
- **Trusted contacts** get autonomous responses (Mode C), escalating to the user only when the agent can't answer confidently

### Per-Contact Capability Scoping

Trusted contacts' requests are handled by sessions with full tool access. Known contacts get a restricted whitelist (read-only filesystem, search — no bash, no web). Configurable per tier.

### Response Attribution

Every response includes how it was generated:
- `"autonomous"` — Agent answered without human involvement
- `"user_response"` — Human answered directly
- `"escalated_user_response"` — Agent tried, couldn't answer, human responded
- `"dismissed"` — Human declined to answer

### Async-Aware

CLI sessions are async by default — the `realtimeResponse` capability in the Agent Card tells senders not to block-wait. Sends to async agents return immediately; responses arrive automatically when available.

## Installation

### Add to your bundle

Create a `.amplifier/bundle.md` in your project directory:

```yaml
---
bundle:
  name: my-a2a-session
  version: 0.1.0

includes:
  - bundle: amplifier-dev  # or your base bundle
  - bundle: git+https://github.com/microsoft/amplifier-bundle-a2a@main#subdirectory=behaviors/a2a.yaml

hooks:
  - module: hooks-a2a-server
    config:
      port: 8222
      agent_name: "My Agent"
      agent_description: "My personal assistant"
      discovery:
        mdns: true
      known_agents:
        - name: "Friend's Agent"
          url: "http://friend-laptop.local:8223"

tools:
  - module: tool-a2a
    config: {}  # sender identity auto-derived from hook
---
```

Then set it as your active bundle in `.amplifier/settings.yaml`:

```yaml
bundle:
  active: my-a2a-session
  added:
    my-a2a-session: "file:///path/to/your/project/.amplifier"
```

### For local development

Add source overrides to `.amplifier/settings.yaml` to use local module code:

```yaml
sources:
  tool-a2a: file:///path/to/amplifier-bundle-a2a/modules/tool-a2a
  hooks-a2a-server: file:///path/to/amplifier-bundle-a2a/modules/hooks-a2a-server
```

## Usage

### Connect to another agent

```
> What's my A2A address?

Agent calls: a2a(operation="whoami")

"Your agent is 'bkrabach's Agent' at http://192.168.1.42:8222.
 Share this URL with anyone who wants to connect."

> My friend Sarah wants to add my agent. She has her own Amplifier session.

"Tell Sarah to say: 'Add my friend's agent at http://192.168.1.42:8222'"

[In Sarah's session]
> Add my friend's agent at http://192.168.1.42:8222

Agent calls: a2a(operation="add_contact", url="http://192.168.1.42:8222")

"Added bkrabach's Agent as a contact."
```

### Discover agents on your network

```
> List the available A2A agents

Agent calls: a2a(operation="discover")  # scans LAN via mDNS
Agent calls: a2a(operation="agents")    # merges all sources

"I found 2 agents:
  - Sarah's Agent (http://sarah-laptop.local:8222)
  - Work Agent (http://work-pc.tailscale:8222)"
```

### Send a message

```
> Ask Sarah's agent what restaurant she wants tonight

Agent calls: a2a(operation="send", agent="Sarah's Agent",
               message="What restaurant does Sarah want tonight?")

"Sarah's agent is async — message delivered.
 The response will arrive when Sarah is available."

[Later, response arrives automatically]

"Sarah's agent responded: 'Sushi Nozawa!'
 (Answered by Sarah personally)"
```

### Handle incoming messages

Messages from remote agents appear automatically in your session:

```
[Incoming from Ben's Agent]
"What restaurant do you want tonight?"

> Tell him Sushi Nozawa

Agent calls: a2a(operation="respond", task_id="abc-123",
               message="Sushi Nozawa!")
```

### Manage contacts

```
> Show my contacts
> Upgrade Sarah to trusted
> Approve the new agent as known
> Block that agent
```

## Tool Operations

| Operation | Purpose |
|-----------|---------|
| `whoami` | Show your agent's name, URL, and status |
| `add_contact` | Add a remote agent by URL (fetches their card) |
| `agents` | List all known agents (config + mDNS + contacts) |
| `discover` | Scan LAN for agents via mDNS |
| `card` | Fetch a remote agent's identity card |
| `send` | Send a message (auto-detects async agents) |
| `status` | Check status of an async task |
| `respond` | Reply to an incoming message |
| `dismiss` | Reject an incoming message |
| `defer` | "Not now" — handle later |
| `approve` | Allow a new agent to contact you |
| `block` | Block a new agent |
| `contacts` | List your contacts and trust tiers |
| `trust` | Change a contact's trust tier |

## Architecture

The bundle ships two inline modules:

```
amplifier-bundle-a2a/
├── bundle.md                         # Thin root bundle
├── behaviors/
│   └── a2a.yaml                      # Composes tool + hook + context
├── context/
│   └── a2a-instructions.md           # LLM instructions (14 operations)
├── modules/
│   ├── tool-a2a/                     # CLIENT: sends messages to remote agents
│   │   └── amplifier_module_tool_a2a/
│   │       ├── __init__.py           # A2ATool (14 operations)
│   │       ├── client.py             # A2A HTTP client
│   │       └── discovery.py          # mDNS browsing
│   └── hooks-a2a-server/             # SERVER: receives messages from remote agents
│       └── amplifier_module_hooks_a2a_server/
│           ├── __init__.py           # mount() — starts HTTP server
│           ├── server.py             # A2AServer (aiohttp)
│           ├── registry.py           # Shared state (tasks, agents, cards)
│           ├── card.py               # Agent Card generation
│           ├── contacts.py           # Contact list with trust tiers
│           ├── pending.py            # Pending message/approval queues
│           ├── injection.py          # Mode B live injection handler
│           ├── evaluation.py         # LLM confidence evaluation
│           └── discovery.py          # mDNS advertisement
└── tests/                            # 290 tests
```

### How It Works

1. **On session start**, the hook module starts an HTTP server and advertises via mDNS
2. **When sending**, the tool resolves the agent URL, fetches the Agent Card, and sends via HTTP
3. **When receiving**, the server checks the sender against the contact list, routes to Mode C (autonomous) or Mode A (queue for user), and returns an A2A task
4. **For live sessions**, the injection handler presents pending messages on each LLM turn (Mode B)
5. **For async responses**, a background poller detects completed remote tasks and injects them into the sender's session

## Configuration

### Server (hooks-a2a-server)

| Option | Default | Description |
|--------|---------|-------------|
| `port` | `8222` | HTTP server port. If port 8222 is in use, you'll see a clear error suggesting a different port |
| `host` | `0.0.0.0` | Bind address |
| `agent_name` | `"$USER's Agent"` | Display name in Agent Card. Defaults to `$USER's Agent` (e.g., "bkrabach's Agent") |
| `agent_description` | `"An Amplifier-powered agent"` | Description in Agent Card |
| `skills` | `[]` | Skills advertised in Agent Card |
| `known_agents` | `[]` | Pre-configured remote agents `[{name, url}]` |
| `realtime_response` | `false` | Whether this agent can respond in real-time |
| `confidence_evaluation` | `true` | Enable LLM confidence check for Mode C |
| `discovery.mdns` | `true` | Enable mDNS advertisement |
| `trust_tiers.trusted.tools` | `"*"` | Tool whitelist for trusted contacts |
| `trust_tiers.known.tools` | `["tool-filesystem", "tool-search"]` | Tool whitelist for known contacts |

### Client (tool-a2a)

| Option | Default | Description |
|--------|---------|-------------|
| `default_timeout` | `30` | Blocking send timeout (seconds) |
| `poll_interval` | `5` | Background poller interval (seconds) |
| `sender_url` | *(auto-derived)* | Auto-derived from the server hook's registry. Only needed as an escape hatch if the tool can't find the hook |
| `sender_name` | *(auto-derived)* | Auto-derived from the server hook's registry. Only needed as an escape hatch if the tool can't find the hook |

## Network Requirements

| Network | Discovery | Config Needed |
|---------|-----------|---------------|
| Same LAN | mDNS (automatic) | None |
| Tailscale | Manual | Add hostname to `known_agents` |
| VPN / VLAN | Manual | Add hostname/IP to `known_agents` |
| Internet | Manual | Add public URL (needs port forwarding) |

## Development

### Run tests

```bash
cd amplifier-bundle-a2a
python3 -m venv .venv
source .venv/bin/activate
pip install -e ../amplifier-core  # peer dependency
pip install pytest pytest-asyncio aiohttp zeroconf
pip install -e modules/hooks-a2a-server
pip install -e modules/tool-a2a
pytest -v
```

### Run the demo

```bash
source .venv/bin/activate
python demo_a2a.py
```

Starts two agents on localhost and demonstrates Mode A (human-in-the-loop), Mode C (autonomous), and first-contact approval — all with real HTTP.

## Getting Help

For general Amplifier questions and discussion:

- [Amplifier GitHub](https://github.com/microsoft/amplifier)
- [Amplifier Documentation](https://github.com/microsoft/amplifier/tree/main/docs)

For questions about the A2A protocol itself:

- [A2A Protocol Specification](https://a2a-protocol.org/)

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

## License

MIT License - see [LICENSE](LICENSE) for details.
