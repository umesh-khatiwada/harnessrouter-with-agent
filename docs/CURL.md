# HarnessRouter with curl

Every call the examples and `tests/run_tests.sh` make, as copy-paste curl commands. They need `curl`
and `jq`. Each command was run against a local HarnessRouter with an NVIDIA integration
(see [TEST_RESULTS.md](TEST_RESULTS.md)).

## 0. Setup

Load the settings from the repo-root `.env` into your shell:

```bash
set -a; source .env; set +a
```

Short names used below (change `MODEL` to `nemotron-3.5-lightning` for the fast model):

```bash
B=$HARNESSROUTER_BASE_URL; AUTH="Authorization: Bearer $HARNESSROUTER_API_KEY"; HARNESS=hermes; MODEL=nemotron-3-ultra
```

## 1. Is the server up?

No key needed for the schema:

```bash
curl -s "$B/v1/openapi.json" | jq '.info, (.paths | length)'
```

Everything else needs the key. Without it you get `401`:

```bash
curl -s -w ' HTTP %{http_code}\n' "$B/v1/bases"
```

## 2. What can this instance run?

Bases (built-in harnesses), their status and available models:

```bash
curl -s "$B/v1/bases" -H "$AUTH" | jq -r '.bases[] | select(.status=="ready") | "\(.id)\t\([.models[] | select(.available) | .id] | join(", "))"'
```

All models per backend (`hermes`, `claude`, `codex`, ...):

```bash
curl -s "$B/v1/models" -H "$AUTH" | jq '.backends.hermes.models | map(select(.available) | .id)'
```

## 3. Run a task

`metadata.harness_id` picks the agent, `model` picks the model:

```bash
curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Reply with exactly: ok","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"}}' | tee /tmp/hr_resp.json | jq '{id, status, model, served_model, connection, usage}'
```

Just the answer text:

```bash
jq -r '[.output[] | select(.type=="message") | .content[] | select(.type=="output_text") | .text] | join("")' /tmp/hr_resp.json
```

`served_model` (`nvidia/nemotron-3-ultra-550b-a55b`) and `connection` (`integration:nvidia`) prove
which provider actually ran it.

## 4. Stream it (SSE)

`-N` turns off curl's buffering. Each event is a `data:` line of JSON:

```bash
curl -sN "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Create hello.py that prints hello, run it, and tell me the output.","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"},"stream":true}' \
  | sed -un 's/^data: //p' | jq -rc 'select(.type | test("output_item.done|completed|failed|incomplete")) | [.sequence_number, .type, (.item.name // .response.status // "")] | @tsv'
```

Print only the answer text as it arrives:

```bash
curl -sN "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Explain an agent harness in 3 sentences.","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"},"stream":true}' \
  | sed -un 's/^data: //p' | jq -j --unbuffered 'select(.type=="response.output_text.delta") | .delta'; echo
```

Rules: `sequence_number` starts at 0 with no gaps; exactly one terminal event
(`response.completed`, `response.incomplete` or `response.failed`); trust `.response.status`, not
the event name (a cancel ends in `response.failed` with `status: "cancelled"`).

## 5. Continue a session

Turn 1, keep its id:

```bash
R1=$(curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Remember this codeword: PELICAN. Reply with exactly: saved","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"}}' | jq -r .id); echo "$R1"
```

Turn 2 continues it with `previous_response_id` (same session, same workspace):

```bash
curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"What was the codeword?","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"},"previous_response_id":"'$R1'"}' \
  | tee /tmp/hr_resp.json | jq -r '.metadata.session_id, ([.output[] | select(.type=="message") | .content[].text] | join(""))'
```

List the session's turns:

```bash
SID=$(jq -r .metadata.session_id /tmp/hr_resp.json); curl -s "$B/v1/sessions/$SID/turns" -H "$AUTH" | jq '.turns | length'
```

## 6. Files in, artifacts out

Upload (multipart, field `file`, 25 MiB max):

```bash
FID=$(curl -s "$B/v1/files" -H "$AUTH" -F "file=@examples/python/sample_data/sales.csv;type=text/csv" -F purpose=user_data | jq -r .id); echo "$FID"
```

A task that reads it and writes `report.md`:

```bash
curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"},
  "input":[{"role":"user","content":[
    {"type":"input_text","text":"Read sales.csv. Write report.md with total revenue per region, high to low. Reply with the top region only."},
    {"type":"input_file","file_id":"'$FID'"}]}]}' | tee /tmp/hr_resp.json | jq -r '.status, .metadata.session_id'
```

Files the last turn changed (the session id is also the `container_id`):

```bash
SID=$(jq -r .metadata.session_id /tmp/hr_resp.json); curl -s "$B/v1/sessions/$SID/files?changed=true" -H "$AUTH" | jq -r '.files[] | "\(.file_id)\t\(.filename)\t\(.bytes)"'
```

Download one:

```bash
RFID=$(curl -s "$B/v1/sessions/$SID/files?changed=true" -H "$AUTH" | jq -r '.files[] | select(.filename=="report.md") | .file_id'); curl -s "$B/v1/containers/$SID/files/$RFID/content" -H "$AUTH" -o report.md && cat report.md
```

All of the session's files as one zip:

```bash
curl -s "$B/v1/sessions/$SID/files/archive" -H "$AUTH" -o session.zip && unzip -l session.zip
```

## 7. Your own configured harness

Create one. `Idempotency-Key` makes a retried create return the same harness, not a second one:

```bash
HID=$(curl -s "$B/v1/harnesses" -H "$AUTH" -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name":"Terse helper","base":"'$HARNESS'","default_model":"'$MODEL'","system_prompt":"Answer in at most 12 words.","max_step":10,"timeout_seconds":300}' | jq -r .id); echo "$HID"
```

Models it can run, then a task with no `model` (the harness default is used):

```bash
curl -s "$B/v1/harnesses/$HID/models" -H "$AUTH" | jq '[.models[] | select(.available) | .id]'
```

```bash
curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Name one benefit of an agent harness.","metadata":{"harness_id":"'$HID'"}}' | jq -r '.model, ([.output[] | select(.type=="message") | .content[].text] | join(""))'
```

List and delete:

```bash
curl -s "$B/v1/harnesses" -H "$AUTH" | jq -r '.harnesses[] | "\(.id)\t\(.base)\t\(.name)"'
```

```bash
curl -s -X DELETE "$B/v1/harnesses/$HID" -H "$AUTH"
```

## 8. Background tasks and cancel

Start without holding a connection (returns at once, `status: in_progress`):

```bash
RID=$(curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"input":"Count slowly from 1 to 200, running sleep 1 between numbers.","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"},"background":true}' | jq -r .id); echo "$RID"
```

Poll:

```bash
curl -s "$B/v1/responses/$RID" -H "$AUTH" | jq -r .status
```

Cancel (safe to repeat), then poll again until `cancelled`:

```bash
curl -s -X POST "$B/v1/responses/$RID/cancel" -H "$AUTH" | jq -r .status
```

Closing a connection does **not** stop a task; only cancel does.

## 9. Safe retries

Send the same `Idempotency-Key` on every retry of the same POST; the task runs once:

```bash
KEY=$(uuidgen); for i in 1 2; do curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY" -d '{"input":"Reply with exactly: once","model":"'$MODEL'","metadata":{"harness_id":"'$HARNESS'"}}' | jq -r .id; done
```

Reusing a key with a different body returns `409 conflict`.

## 10. Errors you should handle

| Call | Answer |
|---|---|
| No / wrong API key | `401` `invalid_credential` |
| Model the harness can't run | `422` `model_unavailable` |
| Unknown `harness_id` | `404` `harness_not_found` |
| NVIDIA model on `codex` | `422` `model_unavailable` |
| Same Idempotency-Key, different body | `409` `conflict` |
| Provider refused the key (task runs, then fails) | `200`, `status: failed`, `error.code: turn_failed` |

```bash
curl -s "$B/v1/responses" -H "$AUTH" -H 'Content-Type: application/json' -d '{"input":"hi","model":"not-a-model","metadata":{"harness_id":"'$HARNESS'"}}' | jq .error
```

## 11. The NVIDIA connection

Check an NVIDIA key directly, without HarnessRouter (`403` = bad key, `404 ... for account` = the
model isn't enabled for that account):

```bash
curl -s https://integrate.api.nvidia.com/v1/chat/completions -H "Authorization: Bearer $NVIDIA_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"nvidia/nemotron-3-ultra-550b-a55b","max_tokens":1500,"messages":[{"role":"user","content":"Reply with exactly: ok"}]}' | jq '.choices[0].message.content // .'
```

See what HarnessRouter has configured (keys come back as `__secret__`):

```bash
curl -s "$B/v1/admin/integrations" -H "$AUTH" | jq '{integrations: [.integrations[] | {name, provider, config, models}], model_map}'
```

Add or replace the NVIDIA integration. **This PUT replaces the whole list**, so include any other
integrations you have (an existing key can be sent back as `"__secret__"` to keep it):

```bash
curl -s -X PUT "$B/v1/admin/integrations" -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "integrations":[{"name":"nvidia","provider":"custom",
    "config":{"api_format":"openai","base_url":"https://integrate.api.nvidia.com/v1","api_key":"'$NVIDIA_API_KEY'"},
    "models":[{"canonical":"nemotron-3-ultra","provider_id":"nvidia/nemotron-3-ultra-550b-a55b"},
              {"canonical":"nemotron-3.5-lightning","provider_id":"nvidia/nemotron-3.5-lightning-30b-a3b"}]}]}' | jq '.ok, .model_map'
```

## 12. Run the whole suite

```bash
bash tests/run_tests.sh
```

```bash
SHOW_CURL=1 ONLY=T05,T07 bash tests/run_tests.sh
```

```bash
NVIDIA_API_KEY=nvapi-... HR_TEST_MODEL=nemotron-3.5-lightning bash tests/run_tests.sh
```
