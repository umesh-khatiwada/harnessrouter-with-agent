#!/usr/bin/env bash
# HarnessRouter end-to-end test suite: plain curl + jq, no Python or Node needed.
#
# Runs real agent tasks through your HarnessRouter instance and checks every feature the examples
# use: auth, bases, tasks, streaming, sessions, files, configured harnesses, background + cancel,
# idempotency and error codes. Each run writes a Markdown report and every raw response to
# tests/results/<timestamp>/.
#
# Usage (from the repo root):
#   bash tests/run_tests.sh                    # full suite, settings from .env
#   SHOW_CURL=1 bash tests/run_tests.sh        # also print each curl command (key not shown)
#   ONLY=T06,T07 bash tests/run_tests.sh       # run just these tests
#   SKIP=T11 bash tests/run_tests.sh           # skip these tests
#   HR_TEST_MODEL=nemotron-3.5-lightning bash tests/run_tests.sh
#
# Settings (environment, or the repo-root .env):
#   HARNESSROUTER_BASE_URL   default http://localhost:3000/api/harness
#   HARNESSROUTER_API_KEY    required (Console -> API Keys)
#   HR_TEST_HARNESS          default hermes
#   HR_TEST_MODEL            default $HR_NVIDIA_MODEL, else nemotron-3-ultra
#   NVIDIA_API_KEY           optional: adds T14, a direct call to NVIDIA that bypasses HarnessRouter
#   HR_TEST_TIMEOUT          seconds per request, default 600
#
# Exit code: the number of failed tests (0 = all passed).
# Works with macOS's stock bash 3.2. Needs curl and jq.

set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -f "$ROOT/.env" ]; then
  # Read .env without letting it override variables already set in the shell.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    k=${line%%=*}; v=${line#*=}
    [ -z "${!k:-}" ] && export "$k=$v"
  done < "$ROOT/.env"
fi

BASE=${HARNESSROUTER_BASE_URL:-http://localhost:3000/api/harness}
BASE=${BASE%/}
KEY=${HARNESSROUTER_API_KEY:-}
HARNESS=${HR_TEST_HARNESS:-hermes}
MODEL=${HR_TEST_MODEL:-${HR_NVIDIA_MODEL:-nemotron-3-ultra}}
TIMEOUT=${HR_TEST_TIMEOUT:-600}
SHOW_CURL=${SHOW_CURL:-0}
ONLY=${ONLY:-}
SKIP=${SKIP:-}

for bin in curl jq; do
  command -v "$bin" >/dev/null || { echo "missing '$bin' (macOS: brew install $bin)"; exit 127; }
done
[ -n "$KEY" ] || { echo "HARNESSROUTER_API_KEY is not set (put it in .env or export it)"; exit 2; }

RUN_DIR="$ROOT/tests/results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR/logs"
LAST="$RUN_DIR/.last"
ROWS="$RUN_DIR/.rows"
: > "$ROWS"

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'; else G= R= Y= D= N=; fi

# ── helpers ──────────────────────────────────────────────────────────────────────────────────
CUR=""          # current test id
NCALL=0         # call counter inside a test, for log file names
AUTH="key"      # key | none | wrong
IDEM=""         # Idempotency-Key for the next api call
RESULT=""; DETAIL=""

# api METHOD PATH [JSON]: response body -> $LAST (and logs/), status code -> $CODE
api() {
  local method=$1 path=$2 body=${3:-}
  local args=(-sS -X "$method" "$BASE$path" -o "$LAST" -w '%{http_code}' --max-time "$TIMEOUT")
  local shown="curl -sS -X $method \"\$HARNESSROUTER_BASE_URL$path\""
  case "$AUTH" in
    key)   args+=(-H "Authorization: Bearer $KEY"); shown+=" -H \"Authorization: Bearer \$HARNESSROUTER_API_KEY\"" ;;
    wrong) args+=(-H "Authorization: Bearer sk-hr-wrong"); shown+=" -H \"Authorization: Bearer sk-hr-wrong\"" ;;
  esac
  if [ -n "$IDEM" ]; then args+=(-H "Idempotency-Key: $IDEM"); shown+=" -H \"Idempotency-Key: $IDEM\""; fi
  if [ -n "$body" ]; then
    args+=(-H 'Content-Type: application/json' --data "$body")
    shown+=" -H 'Content-Type: application/json' -d '$body'"
  fi
  [ "$SHOW_CURL" = 1 ] && printf '%s    $ %s%s\n' "$D" "$shown" "$N"
  CODE=$(curl "${args[@]}" 2>"$RUN_DIR/.curl_err") || CODE=${CODE:-000}
  [ -s "$LAST" ] || : > "$LAST"
  NCALL=$((NCALL + 1))
  cp "$LAST" "$RUN_DIR/logs/$CUR-$NCALL-$(echo "$method$path" | tr -c 'A-Za-z0-9' '_' | cut -c1-60).json"
}

j()        { jq -r "$@" "$LAST" 2>/dev/null; }                     # j '.status'
out_text() { j '[.output[]? | select(.type=="message") | .content[]? | select(.type=="output_text") | .text] | join("")'; }
err_code() { j '.error.code // .detail // empty'; }
short()    { printf '%s' "$1" | tr '\n' ' ' | cut -c1-${2:-120}; }
ok()       { RESULT=PASS; DETAIL=$1; }
bad()      { RESULT=FAIL; DETAIL=$1; }
need()     { # need CODE: fail the test unless the last call returned it
  [ "$CODE" = "$1" ] && return 0
  bad "expected HTTP $1, got $CODE: $(short "$(err_code) $(cat "$LAST")" 200)"; return 1
}
task_json() { # task_json INPUT [extra JSON object merged in]
  local extra=${2:-}; [ -n "$extra" ] || extra='{}'
  jq -nc --arg i "$1" --arg m "$MODEL" --arg h "$HARNESS" --argjson x "$extra" \
    '{input:$i, model:$m, metadata:{harness_id:$h}} + $x'
}
completed() { # completed: fail unless the last response is status=completed
  local st; st=$(j '.status')
  [ "$st" = completed ] && return 0
  bad "status=$st error=$(short "$(j -c '.error')" 200)"; return 1
}
served() { echo "served_model=$(j '.served_model // "?"') via $(j '.connection // "?"'), tokens in=$(j '.usage.input_tokens // "?"') out=$(j '.usage.output_tokens // "?"')"; }

selected() {
  local id=$1
  if [ -n "$ONLY" ]; then case ",$ONLY," in *",$id,"*) ;; *) return 1 ;; esac; fi
  case ",$SKIP," in *",$id,"*) return 1 ;; esac
  return 0
}

PASSED=0; FAILED=0; SKIPPED=0
run() { # run ID "Name" function
  local id=$1 name=$2 fn=$3
  if ! selected "$id"; then
    SKIPPED=$((SKIPPED + 1)); printf '%-4s %s%-6s%s %s\n' "$id" "$Y" SKIP "$N" "$name"
    printf '%s\t%s\tSKIP\t-\t%s\n' "$id" "$name" "not selected" >> "$ROWS"; return
  fi
  CUR=$id; NCALL=0; AUTH=key; IDEM=""; RESULT=""; DETAIL=""
  if [ "$SHOW_CURL" = 1 ]; then printf '%-4s %s\n' "$id" "$name"; printf '%-4s %-44s ' "" "->"
  else printf '%-4s %-44s ' "$id" "$name"; fi
  local t0=$SECONDS
  "$fn"
  local dt=$((SECONDS - t0))
  [ -n "$RESULT" ] || bad "test did not report a result"
  if [ "$RESULT" = PASS ]; then PASSED=$((PASSED + 1)); printf '%sPASS%s %3ss  %s\n' "$G" "$N" "$dt" "$(short "$DETAIL" 110)"
  elif [ "$RESULT" = SKIP ]; then SKIPPED=$((SKIPPED + 1)); printf '%sSKIP%s %3ss  %s\n' "$Y" "$N" "$dt" "$DETAIL"
  else FAILED=$((FAILED + 1)); printf '%sFAIL%s %3ss  %s\n' "$R" "$N" "$dt" "$(short "$DETAIL" 300)"; fi
  printf '%s\t%s\t%s\t%ss\t%s\n' "$id" "$name" "$RESULT" "$dt" "$(printf '%s' "$DETAIL" | tr '\n\t|' '  /')" >> "$ROWS"
}

# ── tests ────────────────────────────────────────────────────────────────────────────────────
t01_reachable() {
  AUTH=none; api GET /v1/openapi.json; need 200 || return
  ok "$(j '.info.title') v$(j '.info.version'), $(j '.paths | length') paths"
}

t02_auth() {
  AUTH=none;  api GET /v1/bases; local a=$CODE
  AUTH=wrong; api POST /v1/responses '{"input":"hi"}'; local b=$CODE c; c=$(err_code)
  [ "$a" = 401 ] && [ "$b" = 401 ] || { bad "no key -> $a, wrong key -> $b (want 401 and 401)"; return; }
  ok "no key -> 401, wrong key -> 401 $c"
}

t03_bases() {
  api GET /v1/bases; need 200 || return
  local st models
  st=$(j --arg h "$HARNESS" '.bases[] | select(.id==$h) | .status')
  models=$(j --arg h "$HARNESS" '[.bases[] | select(.id==$h) | .models[] | select(.available) | .id] | join(", ")')
  [ "$st" = ready ] || { bad "base $HARNESS status='$st'"; return; }
  case ", $models," in *", $MODEL,"*) ok "$HARNESS ready; available: $models" ;;
                       *) bad "$MODEL not available on $HARNESS (available: ${models:-none})" ;; esac
}

t04_models() {
  # GET /v1/models lists every backend's models. A built-in harness id (hermes) maps to a backend
  # name (hermes; claude-code -> claude) that /v1/bases reports. /v1/harnesses/{id}/models is for
  # configured harnesses (chrn_...) and is exercised in T10.
  api GET /v1/bases; need 200 || return
  local backend; backend=$(j --arg h "$HARNESS" '.bases[] | select(.id==$h) | .backend')
  api GET /v1/models; need 200 || return
  local avail; avail=$(j --arg b "${backend:-$HARNESS}" '[.backends[$b].models[]? | select(.available) | .id] | join(", ")')
  case ", $avail," in *", $MODEL,"*) ok "backend $backend: $avail" ;; *) bad "$MODEL missing from backend $backend: ${avail:-none}" ;; esac
}

t05_task() {
  api POST /v1/responses "$(task_json 'Reply with exactly: ok')"; need 200 || return
  completed || return
  local txt; txt=$(out_text)
  printf '%s' "$txt" | grep -qi 'ok' || { bad "answer was '$(short "$txt")'"; return; }
  ok "answer='$(short "$txt" 30)'; $(served)"
}

t06_sdk_shape() {
  # The fields an OpenAI SDK client reads from a response object.
  api POST /v1/responses "$(task_json 'In one sentence: what is an agent harness?')"; need 200 || return
  completed || return
  local missing; missing=$(j '[("id","object","created_at","status","model","output","usage") as $k | select(has($k)|not) | $k] | join(",")')
  [ -z "$missing" ] || { bad "missing fields: $missing"; return; }
  [ "$(j '.object')" = response ] || { bad "object=$(j .object)"; return; }
  ok "object=response, model=$(j .model), $(out_text | wc -w | tr -d ' ') words"
}

t07_stream() {
  local f="$RUN_DIR/logs/T07-stream.sse" body
  body=$(task_json "Create hello.py that prints 'hello from the harness', run it, and tell me what it printed." '{"stream":true}')
  [ "$SHOW_CURL" = 1 ] && printf '%s    $ curl -sN "$HARNESSROUTER_BASE_URL/v1/responses" -H "Authorization: Bearer $HARNESSROUTER_API_KEY" -H "Content-Type: application/json" -d %s%s\n' "$D" "'$body'" "$N"
  CODE=$(curl -sN "$BASE/v1/responses" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
         --data "$body" -o "$f" -w '%{http_code}' --max-time "$TIMEOUT") || CODE=000
  need 200 || return
  sed -n 's/^data: //p' "$f" | grep -v '^\[DONE\]' | jq -s '.' > "$LAST" 2>/dev/null || { bad "stream was not valid JSON events"; return; }
  local n contiguous terminals status tools text
  n=$(j 'length')
  contiguous=$(j '[.[].sequence_number] as $s | $s == [range(0; $s|length)]')
  terminals=$(j '[.[] | select(.type|test("^response\\.(completed|incomplete|failed)$"))] | length')
  status=$(j '[.[] | select(.type|test("^response\\.(completed|incomplete|failed)$"))][0].response.status')
  tools=$(j '[.[] | select(.type=="response.output_item.done" and .item.type=="function_call") | .item.name] | join(",")')
  text=$(j '[.[] | select(.type=="response.output_text.delta") | .delta] | join("")')
  [ "$contiguous" = true ] || { bad "sequence_number has gaps"; return; }
  [ "$terminals" = 1 ] || { bad "$terminals terminal events (want exactly 1)"; return; }
  [ "$status" = completed ] || { bad "terminal status=$status"; return; }
  printf '%s' "$text" | grep -q 'hello from the harness' || { bad "text did not include the program output: $(short "$text")"; return; }
  ok "$n events, seq 0..$((n - 1)) no gaps, 1 terminal (completed), tools: ${tools:-none}"
}

t08_session() {
  api POST /v1/responses "$(task_json 'Remember this codeword: PELICAN. Also create notes.md containing the line: turn 1 was here. Reply with exactly: saved')"
  need 200 || return; completed || return
  local r1 s1; r1=$(j .id); s1=$(j .metadata.session_id)
  api POST /v1/responses "$(task_json 'What was the codeword? Also append the line "turn 2 was here" to notes.md and show its contents.' \
       "$(jq -nc --arg p "$r1" '{previous_response_id:$p}')")"
  need 200 || return; completed || return
  local s2 txt; s2=$(j .metadata.session_id); txt=$(out_text)
  [ "$s1" = "$s2" ] || { bad "session changed: $s1 -> $s2"; return; }
  printf '%s' "$txt" | grep -qi pelican || { bad "turn 2 forgot the codeword: $(short "$txt")"; return; }
  api GET "/v1/sessions/$s1/turns"; need 200 || return
  local turns; turns=$(j '.turns | length')
  [ "$turns" -ge 2 ] 2>/dev/null || { bad "turns listed: $turns"; return; }
  ok "same session $s1, codeword recalled, $turns turns listed"
}

t09_files() {
  local csv="$ROOT/examples/python/sample_data/sales.csv"
  [ "$SHOW_CURL" = 1 ] && printf '%s    $ curl -sS "$HARNESSROUTER_BASE_URL/v1/files" -H "Authorization: Bearer $HARNESSROUTER_API_KEY" -F "file=@sales.csv;type=text/csv" -F purpose=user_data%s\n' "$D" "$N"
  CODE=$(curl -sS "$BASE/v1/files" -H "Authorization: Bearer $KEY" -F "file=@$csv;type=text/csv" -F purpose=user_data \
         -o "$LAST" -w '%{http_code}' --max-time "$TIMEOUT") || CODE=000
  cp "$LAST" "$RUN_DIR/logs/T09-upload.json"
  need 200 || return
  local fid; fid=$(j .id)
  local body; body=$(jq -nc --arg f "$fid" --arg m "$MODEL" --arg h "$HARNESS" '{model:$m, metadata:{harness_id:$h}, input:[{role:"user", content:[
      {type:"input_text", text:"Read sales.csv. Write report.md with total revenue (units x unit_price) per region, sorted high to low, with two decimals. Reply with the top region only."},
      {type:"input_file", file_id:$f}]}]}')
  api POST /v1/responses "$body"; need 200 || return; completed || return
  local sid answer; sid=$(j .metadata.session_id); answer=$(out_text)
  printf '%s' "$answer" | grep -qi south || { bad "top region answer: $(short "$answer")"; return; }
  api GET "/v1/sessions/$sid/files?changed=true"; need 200 || return
  local rfid; rfid=$(j '[.files[] | select(.filename=="report.md")][0].file_id // empty')
  [ -n "$rfid" ] || { bad "report.md not in changed files: $(j -c '[.files[].filename]')"; return; }
  api GET "/v1/containers/$sid/files/$rfid/content"; need 200 || return
  cp "$LAST" "$RUN_DIR/report.md"
  grep -q '883.43' "$LAST" || { bad "report.md lacks South=883.43: $(short "$(cat "$LAST")" 200)"; return; }
  ok "uploaded $fid, answer='$(short "$answer" 20)', report.md downloaded with South 883.43"
}

t10_harness() {
  local key="test-harness-$(date +%s)" body
  body=$(jq -nc --arg m "$MODEL" --arg h "$HARNESS" '{name:"run_tests.sh harness", base:$h, default_model:$m,
         system_prompt:"You are terse. Answer in at most 12 words.", max_step:10, timeout_seconds:300}')
  IDEM=$key; api POST /v1/harnesses "$body"; need 200 || return
  local hid; hid=$(j .id)
  IDEM=$key; api POST /v1/harnesses "$body"; need 200 || return
  local hid2; hid2=$(j .id); IDEM=""
  [ "$hid" = "$hid2" ] || { bad "retry with same Idempotency-Key made a second harness: $hid vs $hid2"; return; }
  api GET "/v1/harnesses/$hid/models"; need 200 || return
  local avail; avail=$(j '[.models[] | select(.available) | .id] | join(",")')
  case ",$avail," in *",$MODEL,"*) ;; *) bad "harness models lack $MODEL: $avail"; return ;; esac
  api POST /v1/responses "$(jq -nc --arg h "$hid" '{input:"Name one benefit of an agent harness.", metadata:{harness_id:$h}}')"
  need 200 || return; completed || return
  local ran words; ran=$(j .model); words=$(out_text | wc -w | tr -d ' ')
  api DELETE "/v1/harnesses/$hid"
  case "$CODE" in 200|204) ;; *) bad "delete harness -> HTTP $CODE"; return ;; esac
  [ "$ran" = "$MODEL" ] || { bad "task without model ran '$ran', expected default $MODEL"; return; }
  ok "created $hid (retry -> same id), task w/o model ran $ran ($words words), deleted"
}

t11_cancel() {
  api POST /v1/responses "$(task_json 'Count slowly from 1 to 200, running `sleep 1` in the shell between each number.' '{"background":true}')"
  need 200 || return
  local rid st0; rid=$(j .id); st0=$(j .status)
  case "$st0" in queued|in_progress) ;; *) bad "background start status=$st0"; return ;; esac
  sleep 6
  api POST "/v1/responses/$rid/cancel"; need 200 || return
  api POST "/v1/responses/$rid/cancel"; need 200 || return   # cancel is idempotent
  local st="" i
  for i in $(seq 1 40); do
    api GET "/v1/responses/$rid"; st=$(j .status)
    case "$st" in queued|in_progress) sleep 1 ;; *) break ;; esac
  done
  [ "$st" = cancelled ] || { bad "status after cancel=$st"; return; }
  ok "started $st0, cancelled twice (idempotent), final status=cancelled"
}

t12_idempotency() {
  local key="test-resp-$(date +%s)" body; body=$(task_json 'Reply with exactly: once')
  IDEM=$key; api POST /v1/responses "$body"; need 200 || return; local a; a=$(j .id)
  IDEM=$key; api POST /v1/responses "$body"; need 200 || return; local b; b=$(j .id)
  IDEM=$key; api POST /v1/responses "$(task_json 'Reply with exactly: twice')"; local c=$CODE cc; cc=$(err_code)
  [ "$a" = "$b" ] || { bad "same key gave two responses: $a vs $b"; return; }
  [ "$c" = 409 ] || { bad "same key + different body -> $c $cc (want 409)"; return; }
  ok "same key -> same id $a; same key + different body -> 409 $cc"
}

t13_errors() {
  api POST /v1/responses "$(jq -nc --arg h "$HARNESS" '{input:"hi", model:"not-a-model", metadata:{harness_id:$h}}')"
  local c1=$CODE e1; e1=$(err_code)
  api POST /v1/responses '{"input":"hi","metadata":{"harness_id":"no-such-harness"}}'
  local c2=$CODE e2; e2=$(err_code)
  api POST /v1/responses "$(jq -nc --arg m "$MODEL" '{input:"hi", model:$m, metadata:{harness_id:"codex"}}')"
  local c3=$CODE e3; e3=$(err_code)
  [ "$c1:$e1" = "422:model_unavailable" ] || { bad "unknown model -> $c1 $e1"; return; }
  [ "$c2:$e2" = "404:harness_not_found" ] || { bad "unknown harness -> $c2 $e2"; return; }
  [ "$c3" = 422 ] || { bad "$MODEL on codex -> $c3 $e3 (want 422)"; return; }
  ok "bad model 422 $e1; bad harness 404 $e2; $MODEL on codex 422 $e3"
}

t14_nvidia_direct() {
  if [ -z "${NVIDIA_API_KEY:-}" ]; then RESULT=SKIP; DETAIL="NVIDIA_API_KEY not set"; return; fi
  local pid="nvidia/nemotron-3-ultra-550b-a55b"
  [ "$MODEL" = nemotron-3.5-lightning ] && pid="nvidia/nemotron-3.5-lightning-30b-a3b"
  CODE=$(curl -sS https://integrate.api.nvidia.com/v1/chat/completions -H "Authorization: Bearer $NVIDIA_API_KEY" \
         -H 'Content-Type: application/json' -o "$LAST" -w '%{http_code}' --max-time 120 \
         -d "$(jq -nc --arg m "$pid" '{model:$m, max_tokens:1500, messages:[{role:"user", content:"Reply with exactly: ok"}]}')") || CODE=000
  cp "$LAST" "$RUN_DIR/logs/T14-nvidia.json"
  # Reasoning models spend tokens thinking first; a reply cut off there has content=null.
  local said; said=$(j '.choices[0].message.content // empty')
  case "$CODE" in
    200) if [ -n "$said" ]; then ok "NVIDIA answered '$(short "$said" 20)' for $pid"
         else bad "200 but no text (finish_reason=$(j '.choices[0].finish_reason')); raise max_tokens"; fi ;;
    403) bad "403: NVIDIA rejected the key itself" ;;
    404) bad "404: key works but $pid is not enabled for this NVIDIA account" ;;
    *)   bad "HTTP $CODE $(short "$(cat "$LAST")" 150)" ;;
  esac
}

# ── run ──────────────────────────────────────────────────────────────────────────────────────
STARTED=$(date '+%Y-%m-%d %H:%M:%S %Z')
echo "HarnessRouter test suite  ->  $BASE"
echo "harness=$HARNESS  model=$MODEL  results=${RUN_DIR#$ROOT/}"
echo

run T01 "Server reachable (OpenAPI)"            t01_reachable
run T02 "Auth: missing / wrong key rejected"    t02_auth
run T03 "Base is ready, model available"        t03_bases
run T04 "Model list (GET /v1/models)"           t04_models
run T05 "Simple task completes"                 t05_task
run T06 "Response has OpenAI Responses shape"   t06_sdk_shape
run T07 "Streaming (SSE) with tool calls"       t07_stream
run T08 "Session continues (previous_response)" t08_session
run T09 "File in, artifact out"                 t09_files
run T10 "Configured harness lifecycle"          t10_harness
run T11 "Background task + cancel"              t11_cancel
run T12 "Idempotency-Key on tasks"              t12_idempotency
run T13 "Error codes"                           t13_errors
run T14 "Provider key direct (NVIDIA)"          t14_nvidia_direct

TOTAL=$((PASSED + FAILED))
echo
printf 'Result: %s%d passed%s, %s%d failed%s, %d skipped  (%s)\n' "$G" "$PASSED" "$N" "$R" "$FAILED" "$N" "$SKIPPED" "${RUN_DIR#$ROOT/}/results.md"

{
  echo "# HarnessRouter test run"
  echo
  echo "| | |"
  echo "|---|---|"
  echo "| Started | $STARTED |"
  echo "| Endpoint | \`$BASE\` |"
  echo "| Harness | \`$HARNESS\` |"
  echo "| Model | \`$MODEL\` |"
  echo "| Result | **$PASSED / $TOTAL passed**, $FAILED failed, $SKIPPED skipped |"
  echo
  echo "| ID | Test | Result | Time | Detail |"
  echo "|---|---|---|---|---|"
  while IFS=$'\t' read -r id name res dt detail; do
    echo "| $id | $name | $res | $dt | $detail |"
  done < "$ROWS"
  echo
  echo "Raw responses for every call are in \`logs/\`."
} > "$RUN_DIR/results.md"
rm -f "$LAST" "$ROWS" "$RUN_DIR/.curl_err"

exit "$FAILED"
