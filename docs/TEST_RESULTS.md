# Test results: HarnessRouter + NVIDIA

**Result: 14 / 14 passed** on `nemotron-3-ultra`, plus every model-dependent test passed again on
`nemotron-3.5-lightning`. Every task really ran on NVIDIA: each response reports
`connection: integration:nvidia` and NVIDIA's own model id in `served_model`.

Run on 2026-11-10 with [`tests/run_tests.sh`](../tests/run_tests.sh) (curl + jq). The curl commands
behind every test are in [CURL.md](CURL.md).

## Setup under test

| | |
|---|---|
| HarnessRouter | Community Edition, `harnessrouter/harnessrouter:latest` (`sha256:8c9be9b9…`, built 2026-11-10), Docker on macOS, `http://localhost:3000/api/harness` |
| Harness | `hermes` (Hermes Agent 0.19.0) |
| Provider | NVIDIA `https://integrate.api.nvidia.com/v1`, added as a **Custom** integration, API format `openai` |
| Models | `nemotron-3-ultra` → `nvidia/nemotron-3-ultra-550b-a55b`; `nemotron-3.5-lightning` → `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Client | curl 8, jq 1.7, macOS stock bash 3.2 |

## Main run: `nemotron-3-ultra` (22:00, 3 min 14 s total)

| ID | What it checks | Result | Time | Evidence |
|---|---|---|---|---|
| T01 | Server is up, API schema served | PASS | 0 s | HarnessRouter API v1, 47 paths |
| T02 | Missing or wrong API key is refused | PASS | 0 s | both `401`, `invalid_credential` |
| T03 | `hermes` base is ready, model available | PASS | 1 s | available: `nemotron-3.5-lightning`, `nemotron-3-ultra` |
| T04 | `GET /v1/models` lists the model | PASS | 0 s | backend `hermes` lists both |
| T05 | A simple task completes on NVIDIA | PASS | 7 s | answer `ok`; `served_model=nvidia/nemotron-3-ultra-550b-a55b`, 13,755 in / 27 out tokens |
| T06 | Response matches the OpenAI Responses shape | PASS | 9 s | `object=response`; `id`, `status`, `output`, `usage`, ... present |
| T07 | Streaming with real tool use | PASS | 29 s | 37 events, sequence 0–36 with no gaps, one terminal event (`completed`); agent called `write_file` then `terminal` and reported `hello from the harness` |
| T08 | Multi-turn session (`previous_response_id`) | PASS | 65 s | same session id both turns, codeword PELICAN recalled, 2 turns listed |
| T09 | File upload in, artifact out | PASS | 51 s | uploaded `sales.csv`; answer "South — $883.43"; `report.md` downloaded with correct totals |
| T10 | Configured harness: create, retry, run, delete | PASS | 10 s | retry with same Idempotency-Key returned the same `chrn_…`; task with no `model` ran the harness default; delete returned `deleted: true` |
| T11 | Background task, then cancel | PASS | 6 s | started `in_progress`; cancel sent twice; final `cancelled` |
| T12 | Idempotency-Key on tasks | PASS | 10 s | same key → same response id; same key + different body → `409 conflict` |
| T13 | Error codes | PASS | 0 s | bad model `422 model_unavailable`; bad harness `404 harness_not_found`; NVIDIA model on `codex` `422` |
| T14 | NVIDIA key works directly (no HarnessRouter) | PASS | 6 s | NVIDIA answered `ok` |

The agent's report from T09, checked against the CSV by hand:

| Region | Revenue |
|---|---|
| South | 883.43 |
| East | 746.85 |
| North | 685.88 |
| West | 597.80 |

## Second model: `nemotron-3.5-lightning` (21:55)

T03–T12 all passed (T01, T02 and T13 don't depend on the model and were skipped). Lightning was
faster on short tasks (T05 5 s, T07 16 s) but slower on the CSV task (T09 97 s against 51 s) because it
worked through the numbers in more steps. In T07 it used `execute_code` where Ultra used `terminal`.

T14 first came back with no text: Lightning spent its whole 200-token limit on reasoning. The test
now allows 1,500 tokens and fails on an empty answer; the re-run passed (`ok`).

## Offline: the mock server

The same suite against `mock/mock_uhp_server.py` (no Docker, no keys): **13 passed, T14 skipped**.
Running it there first found three gaps in the mock (it accepted any key, had no harness delete,
and missed one way of phrasing the codeword); they're fixed, so offline practice now matches the
real server for everything this suite checks.

## Findings

1. **NVIDIA key problems show up as two different errors.** The first key returned `403
   Authorization failed` on every model, the same as a made-up key. The second key worked but
   returned `404 … Not found for account` for `nemotron-3-super`, `kimi-k2.6` and
   `llama-3.1-nemotron-ultra-253b-v1`: those aren't enabled for this account. The default was
   changed from `nemotron-3-super` to `nemotron-3-ultra` for that reason. HarnessRouter reports the
   provider's refusal as a `failed` task with `error.code: turn_failed`, not as an HTTP error.
2. **Only Hermes can use NVIDIA here.** A custom OpenAI-format endpoint can't drive Codex (needs the
   Responses API) or Claude Code (needs Anthropic's format). HarnessRouter refuses those pairs up
   front with `422`.
3. **Each task carries about 13,700 input tokens of overhead** (Hermes's own instructions and tool
   definitions), even for "reply with ok". Budget for that on NVIDIA usage.
4. **Typical task time is 5–65 s** on Ultra; tool-heavy or multi-turn tasks take longest.
5. **`GET /v1/harnesses/{id}/models` only accepts configured harness ids** (`chrn_…`). For a
   built-in base like `hermes`, use `GET /v1/models` (`.backends.hermes`) or `GET /v1/bases`.
6. **Reusing an Idempotency-Key with a different body returns `409` with code `conflict`.** The
   mock returned `idempotency_key_reused`; it now matches the real server.
7. **Artifact `download_url` is relative** (`/v1/containers/…`); prefix it with the base URL.
8. **System prompts are followed loosely.** The test harness said "at most 12 words"; answers ran
   7 words in the final run and 24 in an earlier one.

## Reproduce

```bash
bash tests/run_tests.sh
```

```bash
NVIDIA_API_KEY=nvapi-... HR_TEST_MODEL=nemotron-3.5-lightning bash tests/run_tests.sh
```

Each run writes `tests/results/<timestamp>/results.md`, the agent's `report.md`, and every raw
response under `logs/` (git-ignored).
