"""Lesson 3: run one task with plain HTTP, so you see exactly what goes over the wire.

    python 01_first_task.py --provider openai
    python 01_first_task.py --provider anthropic
    python 01_first_task.py --provider openrouter
"""
import json

import hr

a = hr.args(__doc__, show_raw={"action": "store_true", "help": "print the full JSON response"})
prof = hr.profile(a.provider)
hr.banner("First task (raw HTTP)", prof)

body = {
    "input": "Reply with exactly this and nothing else: it works.",
    "model": prof.model,
    "metadata": {"harness_id": prof.harness_id},  # <- the one field that picks the harness
    "stream": False,                               # hold the connection until the task ends
}
print("POST /v1/responses")
print(json.dumps(body, indent=2), "\n")

with hr.http() as c:
    resp = hr.check(c.post("/v1/responses", json=body))

print("answer:", hr.output_text(resp))
hr.describe(resp)
if a.show_raw:
    print(json.dumps(resp, indent=2))
