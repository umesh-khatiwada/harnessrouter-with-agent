# HarnessRouter study guide

A course you can work through in order. Each lesson has a short explanation, the example script
that shows it, and an exercise. By the end you can put an agent harness behind your own product
and switch it between OpenAI, Claude (Anthropic) and OpenRouter without changing your code.

Facts here were checked against the HarnessRouter repository (release `v0.41.8`, protocol version
`2026-10-19`) on 2026-11-10. Model ids change often: always confirm with
`GET /v1/harnesses/{id}/models` (Lesson 3).

---

## Lesson 0: The one idea

**A model API gives you a turn. HarnessRouter gives you a task.**

| | Model API (OpenAI, Anthropic, OpenRouter directly) | HarnessRouter |
|---|---|---|
| You send | messages | a job ("analyse this CSV and write a report") |
| You get back | tokens, maybe a tool call *you* must run | a finished result, plus files the agent made |
| Who runs tools | your code | the harness (shell, file edits, MCP tools, skills) |
| Who keeps state | your code (you resend history) | HarnessRouter (a session with a real workspace) |
| Switching vendor | rewrite request/response handling | change `metadata.harness_id` and `model` |

A **harness** is a complete agent program: Codex, Claude Code, Hermes, OpenCode, Gemini CLI and
about 30 others. Each already knows how to plan, call tools, edit files and finish a job.
What they don't share is a way for *your product* to drive them. HarnessRouter wraps all of them
behind one HTTP API, shaped like the **OpenAI Responses API**, so the official `openai` SDKs work
against it.

That API is an open spec called the **Unified Harness Protocol (UHP)**. HarnessRouter Community
Edition (CE) is the Apache-2.0 reference server you run yourself in Docker.

---

## Lesson 1: Architecture

```text
 your product backend                 HarnessRouter container (Docker)
┌──────────────────────┐   HTTPS   ┌──────────────────────────────────────────┐
│ HARNESSROUTER_API_KEY│ ────────▶ │ Console :3000  (UI + API proxy, login)   │
│ POST /v1/responses   │           │     │                                    │
│ metadata.harness_id  │ ◀──SSE─── │ Gateway :8080  Responses API, sessions,  │
└──────────────────────┘           │     │          files, provider routing   │
                                   │ Runner  :8081  runs the harness CLI in a │
                                   │     │          per-session workspace     │
                                   │ /data volume: SQLite, files, secrets     │
                                   └─────┼────────────────────────────────────┘
                                         │ provider key (yours)
                         ┌───────────────┼──────────────────┐
                         ▼               ▼                  ▼
                   api.openai.com  api.anthropic.com  openrouter.ai/api/v1
```

- Only port 3000 is published. The API base is `http://localhost:3000/api/harness`.
- Each session gets its own folder and its own OS user inside the container (not its own container).
- State lives on the `/data` volume. Delete the volume and you delete everything.

### The three credentials (the most common confusion)

| Credential | Who holds it | What it does |
|---|---|---|
| Console password | a person in the browser | manage the instance |
| **Provider key** (OpenAI / Anthropic / OpenRouter) | **HarnessRouter** | pays for model calls |
| **HarnessRouter API key** | **your product backend** | calls the HarnessRouter API |

Your product never sees the provider key. HarnessRouter never needs your product's users' keys.
This repo mirrors that split: `server/harnessrouter.env` holds provider keys, `.env` holds only
the HarnessRouter key.

**Exercise 1.** Draw where each of the three credentials lives in *your* product. Which one must
never reach a browser? (Answer: the HarnessRouter API key and the provider key.)

---

## Lesson 2: Providers, harnesses and models

Three separate choices, and they must fit together:

1. **Provider connection**: where model calls go and whose key pays (OpenAI, Anthropic, OpenRouter, ...).
2. **Harness base**: which agent program runs (`codex`, `claude-code`, `hermes`, ...).
3. **Model**: a *canonical* id (`gpt-5.4-mini`, `claude-sonnet-5`). HarnessRouter translates it to
   each provider's own spelling (OpenRouter's `anthropic/claude-sonnet-4.6`, for example).

Not every provider can drive every harness. The ones you care about:

| Provider connection | Good harness to start with | Model checked in the support matrix | Notes |
|---|---|---|---|
| `openai` | `codex` | `gpt-5.4-mini` | Codex only speaks the Responses format; this is its native home. |
| `anthropic` | `claude-code` | `claude-sonnet-5` | Claude Code's native home. Prompt caching works here. |
| `openrouter` | `hermes` | `claude-sonnet-4.6` | One key, many vendors. Also drives `codex`, and `claude-code` since 2026-10-13 via OpenRouter's Anthropic-shaped `/messages` endpoint (in the code, not yet in the docs' table or the support matrix). |
| `custom` (NVIDIA, `openai` format) | `hermes` | not in the matrix | Any OpenAI-compatible endpoint. Set up in the Console with model rows (`nemotron-3-ultra` → `nvidia/nemotron-3-ultra-550b-a55b`) because a custom endpoint has no built-in name table. Drives Hermes, OpenCode, Pi and others, never Codex or Claude Code. |

Other useful pairings from the provider table in the self-hosting guide:

- `anthropic` also drives Hermes, OpenCode, Pi, goose, Aider, OpenHands and more, but **not Codex**.
- `openai` drives Codex, Hermes, OpenCode, Pi and more, but **not Claude Code**.
- `openrouter` drives almost everything (27 bases), which makes it the easiest way to try many
  harness × model combinations with one key.

A pairing that doesn't fit is refused *before* anything runs, with a sentence that says why, e.g.
`provider anthropic cannot drive codex; providers that can: ...`.

### When you connect more than one provider

If both OpenAI and OpenRouter can serve `gpt-5.4-mini`, which one pays? A model with no explicit
route goes to the **first** integration that can serve it. To control it, set a route
(`model_map` in the integrations document, or **Model routing** in the Console), or a per-backend
**policy** when you configure from environment variables:

```bash
HR_SECRET_GLOBAL_HARNESS_POLICY_CODEX={"chain":["openai"]}
HR_SECRET_GLOBAL_HARNESS_POLICY_CLAUDE={"chain":["anthropic"]}
HR_SECRET_GLOBAL_HARNESS_POLICY_HERMES={"chain":["openrouter"]}
```

See `server/harnessrouter.env.example` for the full set.

**Exercise 2.** You only have an OpenRouter key. Which harness ids can you call? Run
`python 07_custom_harness.py --list-bases` against your instance and compare with the table above.

---

## Lesson 3: Your first task (the wire format)

`examples/python/01_first_task.py` — no SDK, just HTTP, so you see the bytes.

```http
POST /api/harness/v1/responses
Authorization: Bearer $HARNESSROUTER_API_KEY
Content-Type: application/json

{
  "input": "Reply with exactly this and nothing else: it works.",
  "model": "gpt-5.4-mini",
  "metadata": { "harness_id": "codex" },
  "stream": false
}
```

Response (trimmed):

```json
{
  "id": "resp_284e…", "object": "response", "status": "completed",
  "model": "gpt-5.4-mini",
  "output": [{ "type": "message", "role": "assistant",
               "content": [{ "type": "output_text", "text": "it works." }] }],
  "usage": { "input_tokens": 10878, "output_tokens": 34 },
  "metadata": { "session_id": "hsess…" }
}
```

What to notice:

- **`metadata.harness_id`** picks the harness. A built-in base id (`codex`, `claude-code`, `hermes`)
  works directly; a custom harness has an id like `chrn_…` or `hrn_…` (Lesson 7).
- **`input_tokens: 10878`** for a five-word prompt. That is the harness's own system prompt and tool
  definitions. Harnesses are not cheap per call; they are cheap per *finished job*. Prefer
  providers with prompt caching (the support matrix reports cache hit rates per pair).
- **`metadata.session_id`**: every task lives in a session with a workspace on disk.
- **Did the model you asked for run?** Compare `model` with `metadata.requested_model`. A server
  must either refuse (`422 model_unavailable`) or substitute and say so (`model_fallback: true`).
  Never silently. The example prints this.
- **Ignored fields**: `tools` and `include` from the OpenAI API are accepted but ignored, and named in
  `metadata.ignored_fields`. Tools belong on the harness, not the request (Lesson 7).

Discover what you can run:

```http
GET /v1/harnesses                 # your configured harnesses
GET /v1/bases                     # built-in bases and their models
GET /v1/harnesses/{id}/models     # models this harness can run right now ("available": true)
GET /v1/openapi.json              # the whole API, no login needed
```

**Exercise 3.** Run `01_first_task.py` once per provider (`--provider openai|anthropic|openrouter`).
Write down `usage.input_tokens` for each. Why are they different? (Each harness has its own
system prompt and tool set.)

---

## Lesson 4: The same thing with the OpenAI SDK

`examples/python/02_openai_sdk.py` and `examples/typescript/quickstart.ts`.

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:3000/api/harness/v1", api_key=HARNESSROUTER_API_KEY)
resp = client.responses.create(
    model="claude-sonnet-5",
    input="Summarise the README in three bullets.",
    metadata={"harness_id": "claude-code"},
)
print(resp.output_text)
```

This is why the API is Responses-compatible: existing SDKs, stream parsers and UI components keep
working. Always pass `base_url` explicitly. The SDK otherwise reads `OPENAI_BASE_URL` from your
environment and may send your HarnessRouter key to the wrong server.

Use plain HTTP for the HarnessRouter-only endpoints (sessions, session files, harness management);
the SDK has no methods for them.

---

## Lesson 5: Streaming

`examples/python/03_streaming.py` reads Server-Sent Events by hand so you can see every event.

```text
data: {"type":"response.created","sequence_number":0,"response":{...}}
data: {"type":"response.output_item.added","sequence_number":3,"item":{"type":"function_call","name":"shell",...}}
data: {"type":"response.output_text.delta","sequence_number":7,"delta":"Sum"}
data: {"type":"response.completed","sequence_number":42,"response":{...}}
```

Rules worth memorising:

1. `sequence_number` starts at 0 and increases by exactly 1. A gap means a dropped event.
2. Exactly one terminal event: `response.completed`, `response.incomplete` or `response.failed`.
   It carries the **whole** final response, so you can rebuild the result from it alone.
3. A cancelled task ends with `response.failed` but `status: "cancelled"`. **Trust `status`, not the
   event name.**
4. Tool use arrives as `output_item` of type `function_call` (and `function_call_output`). These are
   *observability*: the harness already ran the tool. You never send a tool result back.
5. Reasoning summaries are optional. Many harness × model pairs send none.
6. A dropped connection does **not** stop the task. Re-read `GET /v1/responses/{id}`.
   Treat the stream as a progress bar and the stored response as the truth.
7. Behind a proxy (nginx, Caddy, Cloudflare), **turn off response buffering** or the stream arrives
   all at once at the end. Caddy: `flush_interval -1`.

**Exercise 5.** Kill `03_streaming.py` with Ctrl+C halfway through a long task, then fetch the
response by id with `08_background_and_cancel.py --get <id>`. Did the task finish anyway?

---

## Lesson 6: Sessions (multi-turn)

`examples/python/04_sessions.py`

```json
{ "input": "Now add tests.", "previous_response_id": "resp_…", "metadata": {"harness_id": "codex"} }
```

- `previous_response_id` continues the **same session**: same conversation, same workspace on disk.
  Files from turn 1 are still there in turn 2.
- You don't resend history. HarnessRouter (and the harness) keep it.
- `harness_id` must match the session's harness, or you get `409 harness_mismatch`.
- You *can* switch **model** mid-session (the support matrix tests "switch" for every pair).
- Only one task runs per session at a time: a second gets `409 session_busy`.
- `branch: true` with `previous_response_id` forks a new session from that point.
- Read the history: `GET /v1/sessions/{session_id}/turns`.
- Idle workspaces are removed after 72 h (`HR_WORKSPACE_TTL_HOURS`) and restored from a checkpoint
  on the next turn. That costs time, not work.

**Exercise 6.** In turn 1 ask the agent to create `notes.md`. In turn 2 switch `model` and ask it
to append to `notes.md`. Then list the session's files (Lesson 8).

---

## Lesson 7: Configured harnesses (where tools, skills and prompts live)

`examples/python/07_custom_harness.py`

A **configured harness** is a base plus your settings, saved once and called by id:

```json
POST /v1/harnesses
{
  "name": "CSV analyst",
  "base": "claude-code",
  "default_model": "claude-sonnet-5",
  "system_prompt": "You are a careful data analyst. Always write findings to report.md.",
  "max_step": 30,
  "timeout_seconds": 900
}
```

Other fields: `mcp_servers`, `skills`, `plugins` (Agent Plugins packages), `disabled_tools`,
`env` (values can be `vault:<name>` or `$headers.X-Name`), `additional_headers`,
`reasoning_effort`.

Why tools go here and not on the request: if a request could attach an MCP server, anyone with an
API key could point the agent (which runs with your workspace and credentials) at any endpoint.
The spec's rule: **narrowing per request is safe, widening is escalation.**

Gotchas:

- `PUT /v1/harnesses/{id}` **replaces** the whole config. Read it first, send everything back.
  (Reads use camelCase, writes use snake_case.)
- The base can't be changed after saving. Make a new harness to use another base.
- A `default_model` the base can't run is refused with `422 model_not_available`.
- Send an `Idempotency-Key` on create so a retry doesn't make two harnesses.

**Exercise 7.** Create one harness per provider (`--provider openai|anthropic|openrouter`) with the
*same* system prompt. Your product now switches vendors by swapping one id.

---

## Lesson 8: Files in, artifacts out

`examples/python/06_files_and_artifacts.py`

**In** (two ways):

```json
// inline, small files
{ "type": "input_file", "filename": "sales.csv", "file_data": "data:text/csv;base64,..." }

// upload once (multipart POST /v1/files, max 25 MiB), then reference
{ "type": "input_file", "file_id": "file_abc..." }
```

The file lands in the agent's working directory under its filename. `filename: "inputs/x.pdf"`
puts it in a folder.

**Out:**

- On the response: `annotations` of type `container_file_citation` with a `download_url`.
- By listing: `GET /v1/sessions/{sid}/files` (all files) or `?changed=true` (last turn only).
- Download: `GET /v1/containers/{container_id}/files/{file_id}/content` (raw bytes).
- Everything as one archive: `GET /v1/sessions/{sid}/files/archive`.
- Edit between turns: `GET/PUT/DELETE /v1/sessions/{sid}/files/{path}`.

**Security tip from the README:** never paste long exact strings (tokens, hashes, ids) into the
prompt. Models drop and swap characters. Attach them as a file, or pass them through the harness's
`env` so they are also redacted from the stored record.

---

## Lesson 9: Production behaviour

`examples/python/08_background_and_cancel.py`

| Need | How |
|---|---|
| Don't hold a connection for an hour | `"background": true`, then poll `GET /v1/responses/{id}` |
| Stop work | `POST /v1/responses/{id}/cancel` (or `/v1/sessions/{sid}/cancel`). Ends with `status: "cancelled"`, keeps output, session stays usable. Cancelling twice is fine. |
| Retry safely | Send `Idempotency-Key: <uuid>`. Same key + same body returns the first result, never a second run. Same key + different body = `409`. |
| Budgets | `max_step`, `timeout_seconds` (default 1 h). Hitting one gives `incomplete`, never `completed`. |
| Reasoning | `"reasoning": {"effort": "low"}`. The response's `reasoning.applied` says what the model really got. |
| Tracing | Send W3C `traceparent` / `tracestate`. Model calls join your trace. |
| Timeouts on your side | Streaming: no total timeout, only an idle timeout. Non-streaming: at least `timeout_seconds` + margin. Giving up does **not** stop the task. Cancel it. |

Retry table (from the Errors chapter):

| Error | Retry? |
|---|---|
| 500/502/503/504 | yes, backoff + jitter, **with the same Idempotency-Key** |
| 429 `rate_limited` | yes, after `Retry-After` |
| 409 `session_busy` | yes, after the running task ends |
| other 4xx | no, fix the request |
| a task that ended `failed` | your call. It already spent money. |

Error envelope: `{"error": {"type": "...", "code": "...", "message": "...", "param": ...}}`.
Branch on `code`, not on the HTTP status.

---

## Lesson 10: Choosing a harness and provider

`examples/python/05_compare_providers.py` runs the same job on every profile and prints time,
tokens and the answer.

HarnessRouter's own benchmark (8 harness × model configurations on one task) found the cheapest
and the most expensive differed by 99.8% in cost and 3.2× in latency, and **the winner changed per
task**. So measure on your own tasks.

Rules of thumb:

- **Already on OpenAI** → `codex` + a GPT model. Native, well tested.
- **Already on Anthropic** → `claude-code` + a Claude model. Native, best prompt caching.
- **Want one key and choice** → OpenRouter + `hermes` (or `codex` / `opencode` / `pi`). The support
  matrix shows OpenRouter as one of the most broadly passing columns.
- Check `docs/support-matrix.md` in the HarnessRouter repo for your exact harness × model pair
  before you commit. It records first turn, follow-up, model switch, file artifact and recovery.

---

## Capstone

Build a small "report agent" service in your product:

1. One configured harness per provider, same system prompt (Lesson 7). Store the three ids in config.
2. An endpoint `POST /reports` in your backend that uploads the user's file, starts a
   **background** task with an `Idempotency-Key`, and returns the response id (Lessons 8, 9).
3. An endpoint `GET /reports/{id}/events` that relays the SSE stream to the browser, with buffering
   off. The browser never sees `HARNESSROUTER_API_KEY` (Lessons 1, 5).
4. When the task completes, list `?changed=true` files and offer downloads (Lesson 8).
5. A "Stop" button calling cancel (Lesson 9).
6. A feature flag that picks the provider profile. Run `05_compare_providers.py` on 10 real
   inputs and pick the default by cost and quality (Lesson 10).

---

## Self-check quiz

1. Where does the OpenRouter key go: your backend's `.env`, or HarnessRouter?
2. Which field selects Claude Code vs Codex?
3. What happens to a running task if your client disconnects?
4. Your stream ended with `response.failed`. How do you tell a cancel from a failure?
5. Why can't you pass `tools` on a request to add an MCP server?
6. You retry a timed-out `POST /v1/responses` without `Idempotency-Key`. What can go wrong?
7. How do you know the model you asked for actually ran?
8. Can an `anthropic` connection run the `codex` harness?

<details><summary>Answers</summary>

1. HarnessRouter (Console → Bring your own key, or `HR_SECRET_GLOBAL_HARNESS_CONN_*`).
2. `metadata.harness_id`.
3. Nothing. It keeps running. Re-read it with `GET /v1/responses/{id}`.
4. `response.status == "cancelled"`.
5. Request-level tool grants would let any API caller widen what the agent can reach. Tools are set on the harness.
6. The task runs twice, possibly at the same time, editing the same files and paying twice.
7. Compare `model` with `metadata.requested_model`, and check `metadata.model_fallback`.
8. No. Codex needs OpenAI-compatible Responses providers (`openai`, `openrouter`, Azure, ...).

</details>

---

## Source map (where to read more in the HarnessRouter repo)

| Topic | File |
|---|---|
| Install, providers, API | `docs/self-hosting-guide.md` |
| Which harness runs which model on which provider | `docs/support-matrix.md`, `docs/support-matrix-notes.md` |
| Protocol, one chapter per topic | `protocol/versions/2026-10-19/*.md` (tasks, streaming, sessions, files, errors, harnesses) |
| OpenAPI schema (generate a typed client) | `protocol/schema/uhp-2026-10-19.openapi.yaml` |
| Provider ↔ harness wiring | `gateway/app.py`, `_INTEGRATION_WIRING` and `_PROVIDER_CATALOG` |
| Conformance tests (good examples of every call) | `protocol/conformance/` |
