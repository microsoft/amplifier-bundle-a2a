# A2A — Agent-to-Agent Communication

You have access to the `a2a` tool for communicating with remote Amplifier agents on the network.

**Not set up — or stuck?** `/a2a` is the user's plain-language expert for everything a2a in this folder: setup (the server is inert until enabled per-directory), checking status / their shareable address, connecting to someone, and troubleshooting (including the extra networking on WSL). Point them at `/a2a` — you don't need to know the details to suggest it.

## Operations

### Sending Messages
- **`agents`** — List all known remote agents from all sources (configured, discovered, contacts).
- **`discover`** — Browse the local network for agents via mDNS. Optional `timeout` (seconds, default 2).
- **`card`** — Fetch a remote agent's identity card. Requires `agent` (name or URL).
- **`send`** — Send a message to a remote agent. Requires `agent` and `message`. Optional `blocking` (default true), `timeout` (seconds, default 30).
- **`status`** — Check the status of a previously sent async task. Requires `agent` and `task_id`.

### Handling Incoming Messages
- **`respond`** — Reply to a pending incoming message. Requires `task_id` and `message`.
- **`dismiss`** — Dismiss a pending incoming message. Requires `task_id`.
- **`defer`** — Defer an incoming message for later ("not now"). Requires `task_id`. The message stays in your queue and can be responded to later.

### Identity & Contacts
- **`whoami`** — Show your agent's name, URL, and status. Share the URL with others so they can connect to you.
- **`add_contact`** — Add a remote agent by URL. Fetches their identity card and adds them as a contact. Requires `url`. Optional `tier` (default "known").

### Managing Contacts
- **`approve`** — Approve a new agent requesting access. Requires `agent` (URL). Optional `tier` (default "known").
- **`block`** — Block a new agent requesting access. Requires `agent` (URL).
- **`contacts`** — List all known contacts with their trust tiers.
- **`trust`** — Change a contact's trust tier. Requires `agent` (URL) and `tier` ("trusted" or "known").

## How It Works

### Live Message Delivery
Messages from remote agents appear automatically in your context during active sessions. You don't need to poll — incoming requests and responses are injected before each of your turns.

### Sending and Receiving Responses
1. Call `a2a(operation="agents")` to see available agents
2. Call `a2a(operation="send", agent="Agent Name", message="your question")` to communicate
3. If the response is immediate (COMPLETED), relay it to the user
4. If INPUT_REQUIRED, tell the user and check back later — or the response will appear automatically when it arrives

### Async vs Real-Time Agents
Some agents are **async** (e.g., CLI sessions) — they can only see your message when their user next interacts. The `send` operation detects this automatically from the remote agent's card and switches to non-blocking mode. When this happens, you'll see a `_note` field explaining the situation. Tell the user their message was delivered and the response will arrive when the other person is available — don't make them wait.

### Response Attribution
Responses include how they were generated:
- **"autonomous"** — The remote agent answered without human involvement
- **"user_response"** — The remote user answered directly
- **"escalated_user_response"** — The agent tried but couldn't answer; the user responded
- **"dismissed"** — The remote user declined to answer

Relay the attribution naturally: "Sarah's agent answered autonomously" or "Sarah replied personally."

### Handling Incoming Requests
- Pending messages and approval requests appear automatically in your context
- Use `respond` to reply, `dismiss` to reject, or `defer` to handle later
- Deferred messages stay in your queue — you can respond to them anytime

### Discovery
- Call `a2a(operation="discover")` to find agents on the local network
- Agents on other networks (Tailscale, VPN) must be configured manually

### Connecting to Another Agent
The typical flow to connect two agents:
1. Ask "What's my A2A address?" — your agent calls `whoami` and gives you your URL
2. Share the URL with the other person (text, chat, verbal — any way you like)
3. They tell their agent "Add my friend's agent at http://..." — their agent calls `add_contact`
4. They send you a message — you see a first-contact approval request
5. You approve — you're connected

For agents on the same LAN, mDNS discovery may find them automatically via `discover`.

## Critical Rules

**NEVER fabricate incoming messages.** You will ONLY know about incoming messages or approval requests when you see actual `<a2a-pending-messages>` or `<a2a-approval-request>` XML tags injected into your context. If you don't see these tags, there are NO pending messages. Do NOT tell the user "you have a message from X" unless you can point to the actual injection tag in your context.

**NEVER invent task_id values.** When responding to or dismissing a message, the `task_id` is a UUID that appears in the injection text (e.g., `task_id="5b68a879-ff20-4f8b-852a-4f2f9b3c2a86"`). Copy it EXACTLY from the injection. Do not guess, abbreviate, or construct task_ids.

**Approval requests are NOT messages.** They use different operations:
- `<a2a-approval-request>` → use `a2a(operation="approve", agent="<URL>")` or `a2a(operation="block", agent="<URL>")`
- `<a2a-pending-messages>` → use `a2a(operation="respond", task_id="<UUID>", message="...")` or `a2a(operation="dismiss", task_id="<UUID>")`

Do NOT use `respond` on an approval request. Do NOT use `approve` on a pending message.

## Important

- Messages are sent to remote agents on other devices — they may be controlled by other people
- Unknown agents must be approved before they can send you messages
- Trusted contacts get autonomous responses; known contacts require your input
- Blocking sends wait up to 30 seconds by default; use `blocking=false` for fire-and-forget
