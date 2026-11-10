"""Lesson 5: stream a task's progress as Server-Sent Events, parsed by hand.

Shows every event type, checks sequence_number for gaps, and reads the final status from the
terminal event (a cancel ends with response.failed but status "cancelled").

    python 03_streaming.py --provider openrouter
    python 03_streaming.py --provider anthropic --all-events
"""
import json
import sys

import hr

a = hr.args(__doc__,
            prompt={"default": "Create a file hello.py that prints 'hello from the harness', run it, "
                               "and tell me what it printed."},
            all_events={"action": "store_true", "help": "print every raw event"})
prof = hr.profile(a.provider)
hr.banner("Streaming", prof)

body = {"input": a.prompt, "model": prof.model,
        "metadata": {"harness_id": prof.harness_id}, "stream": True}

expected_seq = 0
final = None

with hr.http() as c, c.stream("POST", "/v1/responses", json=body,
                              headers={"Accept": "text/event-stream"}) as r:
    if r.status_code >= 400:
        r.read()
        hr.check(r)
    for line in r.iter_lines():
        # SSE: "data: {...}" lines carry events; ": keep-alive" comments and blank lines don't.
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        ev = json.loads(data)
        t, seq = ev.get("type"), ev.get("sequence_number")

        if seq is not None:
            if seq != expected_seq:
                print(f"\n[warning] sequence gap: expected {expected_seq}, got {seq}")
            expected_seq = seq + 1

        if a.all_events:
            print(f"\n[{seq}] {t}")

        if t == "response.created":
            print(f"[created] {ev['response']['id']}")
        elif t == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                # The harness is running a tool itself. This is observability, not a request to you.
                print(f"\n[tool] {item.get('name')} ", end="", flush=True)
        elif t == "response.function_call_arguments.done":
            print(ev.get("arguments", "")[:200], flush=True)
        elif t == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call_output":
                out = str(item.get("output", ""))
                print(f"[tool result] {out[:200]}{'...' if len(out) > 200 else ''}")
        elif t == "response.reasoning_summary_text.delta":
            print(f"\033[2m{ev.get('delta', '')}\033[0m", end="", flush=True)  # dim
        elif t == "response.output_text.delta":
            sys.stdout.write(ev.get("delta", ""))
            sys.stdout.flush()
        elif t == "error":
            print(f"\n[error event] {ev.get('code')}: {ev.get('message')}")
        elif t in ("response.completed", "response.incomplete", "response.failed"):
            final = ev["response"]  # the whole final response: enough to render the result alone
            break

print("\n")
if final is None:
    print("Stream ended without a terminal event. The task may still be running: "
          "re-read it with GET /v1/responses/{id}.")
else:
    print(f"terminal status: {final.get('status')}  (trust status, not the event name)")
    hr.describe(final)
