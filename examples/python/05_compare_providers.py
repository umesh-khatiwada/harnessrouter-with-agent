"""Lesson 10: run the same job on OpenAI, Claude and OpenRouter profiles and compare.

Your product code is identical for all three; only harness_id and model change.
A profile whose provider isn't connected fails fast with HarnessRouter's own reason.

    python 05_compare_providers.py
    python 05_compare_providers.py --providers openai,openrouter
"""
import time

import httpx

import hr

a = hr.args(__doc__,
            providers={"default": "openai,anthropic,openrouter"},
            prompt={"default": "Write a Python one-liner that returns the 10th Fibonacci number, run it, "
                               "and reply with only the number."})

rows = []
with hr.http() as c:
    for name in [p.strip() for p in a.providers.split(",") if p.strip()]:
        prof = hr.profile(name)
        print(f"-> {name}: harness={prof.harness_id} model={prof.model} ...", flush=True)
        t0 = time.monotonic()
        r = c.post("/v1/responses", json={
            "input": a.prompt, "model": prof.model, "metadata": {"harness_id": prof.harness_id}})
        secs = time.monotonic() - t0
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(err)
            except (ValueError, httpx.DecodingError):
                msg = r.text[:200]
            rows.append((name, prof.harness_id, prof.model, f"{secs:.1f}s", "-", "-", f"REFUSED: {msg}"))
            continue
        resp = r.json()
        u = resp.get("usage") or {}
        answer = hr.output_text(resp).strip().replace("\n", " ")[:60]
        status = resp.get("status")
        rows.append((name, prof.harness_id, resp.get("model"), f"{secs:.1f}s",
                     str(u.get("input_tokens", "-")), str(u.get("output_tokens", "-")),
                     answer if status == "completed" else f"{status}: {answer}"))

head = ("provider", "harness", "model ran", "time", "in tok", "out tok", "answer")
widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
print()
for row in (head, *rows):
    print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
