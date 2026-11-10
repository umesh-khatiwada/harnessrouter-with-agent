# harnessrouter-with-agent

A study project for [HarnessRouter](https://github.com/HarnessRouter/harnessrouter): run agent
harnesses (Codex, Claude Code, Hermes, ...) behind **one API**, and switch between **OpenAI**,
**Claude (Anthropic)** and **OpenRouter** without changing your product code.

**Start here: [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md)**: 10 short lessons, exercises, a capstone and a quiz.

## HarnessRouter in 30 seconds

```text
your backend ──POST /v1/responses──▶ HarnessRouter ──▶ harness (codex | claude-code | hermes | ...)
             ◀──── SSE progress ────   (Docker)              │ uses YOUR provider key
                                                             ▼
                                              OpenAI | Anthropic | OpenRouter
```

- The API is **OpenAI Responses-compatible**. The official `openai` SDKs work. You pick the agent with
  `metadata.harness_id` and the model with `model`.
- You send a **job**, not a chat turn. The harness runs its own tools (shell, file edits, MCP) in a
  per-session workspace and hands back the answer plus any files it made.
- Provider keys live **inside HarnessRouter**. Your backend only holds a HarnessRouter API key.

| You want | `metadata.harness_id` | `model` | Provider connected in HarnessRouter |
|---|---|---|---|
| OpenAI | `codex` | `gpt-5.4-mini` | `openai` |
| Claude | `claude-code` | `claude-sonnet-5` | `anthropic` |
| OpenRouter | `hermes` | `claude-sonnet-4.6` | `openrouter` |
| NVIDIA (build.nvidia.com) | `hermes` | `nemotron-3-ultra` | a **Custom** integration ([setup](#using-an-nvidia-key)) |

The first three pairs pass every scenario in HarnessRouter's published support matrix; NVIDIA is not in it. Confirm what your
instance can run with `python examples/python/07_custom_harness.py --list-bases`.

## Layout

```text
docs/STUDY_GUIDE.md          the course
docs/CURL.md                 every API call as a copy-paste curl command
docs/TEST_RESULTS.md         latest end-to-end results (NVIDIA, 14/14)
tests/run_tests.sh           curl + jq test suite: bash tests/run_tests.sh
server/                      run HarnessRouter in Docker with your provider keys
  docker-compose.yml
  harnessrouter.env.example  OpenAI / Anthropic / OpenRouter connections + routing
.env.example                 your app's settings (only the HarnessRouter key)
examples/python/             one script per lesson
  01_first_task.py           raw HTTP: see the wire format
  02_openai_sdk.py           same thing via the OpenAI SDK
  03_streaming.py            SSE events: text, tool calls, terminal status
  04_sessions.py             multi-turn with previous_response_id
  05_compare_providers.py    same job on OpenAI vs Claude vs OpenRouter
  06_files_and_artifacts.py  upload a CSV, download the agent's report
  07_custom_harness.py       your own agent: system prompt, default model, budgets
  08_background_and_cancel.py  background tasks, cancel, idempotent retries
examples/typescript/quickstart.ts   task + streamed follow-up with the openai npm package
mock/mock_uhp_server.py      offline stand-in to learn the API without Docker or keys
```

## Test it

```bash
bash tests/run_tests.sh
```

14 checks with curl + jq (auth, tasks, streaming, sessions, files, harnesses, cancel, retries,
errors). Prints PASS/FAIL, exits with the number of failures, and writes a report to
`tests/results/<timestamp>/`. `SHOW_CURL=1` prints each curl command; `ONLY=T05,T07` or `SKIP=T11`
picks tests; `NVIDIA_API_KEY=...` adds a direct provider check. Latest results:
[docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

## Run it for real

You need Docker (about 4 GB disk) and at least one provider key.

**1. Start HarnessRouter with your provider keys**

```bash
cp server/harnessrouter.env.example server/harnessrouter.env
```

Edit `server/harnessrouter.env` and replace the `REPLACE_ME` keys you have (delete the lines you
don't). Then:

```bash
docker compose -f server/docker-compose.yml up -d
```

```bash
docker logs -f harnessrouter
```

Wait for `[harnessrouter] ready on :3000` (the first start installs the harness CLIs), then Ctrl+C.

**2. Get the Console password and an API key**

```bash
docker exec harnessrouter cat /data/selfhost-initial-password
```

Open http://localhost:3000, sign in as `harnessrouter`, change the password in **Profile**, then
**API Keys → Create**. Run one task in the Console first to confirm the provider works.

**3. Configure this project**

```bash
cp .env.example .env
```

Put the key in `.env` as `HARNESSROUTER_API_KEY`.

**4. Run the examples**

```bash
cd examples/python && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

```bash
python 01_first_task.py --provider openai
```

```bash
python 05_compare_providers.py --providers openai,anthropic,openrouter
```

TypeScript:

```bash
cd examples/typescript && npm install && npx tsx quickstart.ts anthropic
```

## Using an NVIDIA key

NVIDIA's API (`https://integrate.api.nvidia.com/v1`, keys start `nvapi-`) is not a built-in
provider. Add it as a **Custom** OpenAI-format integration in the Console, not in
`server/harnessrouter.env`: only an integration's model rows translate HarnessRouter's model name
(`nemotron-3-ultra`) into NVIDIA's (`nvidia/nemotron-3-ultra-550b-a55b`).

1. Console → account menu → **Bring your own key** → add **Custom**:
   - **API Format**: `openai`
   - **Endpoint URL**: `https://integrate.api.nvidia.com/v1` (leave *Use Full URL* off)
   - **API Key**: your `nvapi-...` key
   - **Models** (canonical → provider id):
     - `nemotron-3-ultra` → `nvidia/nemotron-3-ultra-550b-a55b` (default here; strong agent, ~4 s/call)
     - `nemotron-3.5-lightning` → `nvidia/nemotron-3.5-lightning-30b-a3b` (fast, ~1 s/call)
2. Set `HR_PROVIDER=nvidia` in `.env` and run any example, e.g. `python 01_first_task.py`.
   `HR_NVIDIA_MODEL=nemotron-3.5-lightning` switches model.

What works: the **`hermes`** harness (it speaks OpenAI chat/completions). Codex needs the Responses
API and Claude Code needs Anthropic's format, so neither runs on NVIDIA.

**Which models your key can use differs per NVIDIA account.** Test one before adding it:

```bash
curl -s https://integrate.api.nvidia.com/v1/chat/completions -H "Authorization: Bearer $NVIDIA_API_KEY" -H 'Content-Type: application/json' -d '{"model":"nvidia/nemotron-3-ultra-550b-a55b","messages":[{"role":"user","content":"Reply with exactly: ok"}],"max_tokens":50}'
```

`403 Authorization failed` = the key itself is bad. `404 ... Not found for account` = the key works
but that model isn't enabled for your account (on the account tested here, `nemotron-3-super` and
`kimi-k2.6` were 404). A model also needs to be in Hermes's catalog (`python 07_custom_harness.py --list-bases`).

## Learn offline first (no Docker, no keys)

The mock server answers the same endpoints with the same JSON and event shapes, with a fake agent.
Good for reading the protocol; it says nothing about real model quality.

```bash
python3 mock/mock_uhp_server.py
```

In another terminal:

```bash
cd examples/python && HARNESSROUTER_BASE_URL=http://localhost:8787 HARNESSROUTER_API_KEY=mock-key python 03_streaming.py --all-events
```

## Things that trip people up

- **Provider not connected**: `no provider configured for backend 'codex'`. Add the key in the Console
  (account menu → Bring your own key) or in `server/harnessrouter.env`, then restart the container.
- **Wrong pair**: Codex can't run on an Anthropic key and Claude Code can't run on an OpenAI key.
  OpenRouter can drive most harnesses.
- **`OPENAI_BASE_URL` in your shell**: the examples pass `base_url` explicitly so it is ignored. Do the
  same in your code.
- **Stream arrives all at once**: a proxy is buffering. Turn buffering off (Caddy: `flush_interval -1`).
- **Retrying a POST**: always send an `Idempotency-Key`, or a timeout + retry runs the job twice.
- **Disconnecting doesn't stop a task.** Cancel it with `POST /v1/responses/{id}/cancel`.
