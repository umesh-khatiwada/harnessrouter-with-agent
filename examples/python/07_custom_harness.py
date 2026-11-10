"""Lesson 7: create a configured harness (your own agent) and run tasks on it by id.

A configured harness = a base (codex, claude-code, hermes, ...) + your system prompt, default
model, budgets, MCP servers, skills and plugins. Your product then calls it by its id.

    python 07_custom_harness.py --list-bases            # what this instance can run
    python 07_custom_harness.py --provider anthropic    # create "CSV analyst" on claude-code
"""
import uuid

import hr

a = hr.args(__doc__,
            list_bases={"action": "store_true", "help": "list bases and their available models"},
            name={"default": None, "help": "harness name (default: 'Study agent (<provider>)')"})

with hr.http() as c:
    if a.list_bases:
        bases = hr.check(c.get("/v1/bases")).get("bases") or []
        for b in bases:
            avail = [m["id"] for m in b.get("models") or [] if m.get("available")]
            status = b.get("status")
            print(f"{b['id']:<14} {status or '':<14} {len(avail):>3} available model(s)"
                  + (f": {', '.join(avail[:6])}{' ...' if len(avail) > 6 else ''}" if avail else ""))
        raise SystemExit(0)

    prof = hr.profile(a.provider)
    hr.banner("Configured harness", prof)
    base = prof.harness_id  # a built-in base id: codex, claude-code, hermes, ...

    # Idempotency-Key: if this request times out and you retry with the same key,
    # you get the same harness back instead of a duplicate.
    created = hr.check(c.post("/v1/harnesses", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "name": a.name or f"Study agent ({prof.provider})",
        "base": base,
        "default_model": prof.model,
        "system_prompt": "You are a concise senior engineer. Keep answers under 80 words. "
                         "When you create files, list their names at the end.",
        "max_step": 25,
        "timeout_seconds": 600,
        # Also available: "mcp_servers": [...], "skills": [...], "plugins": [...],
        # "disabled_tools": ["web_search"], "env": {"API_TOKEN": "vault:my-token"},
        # "reasoning_effort": "low"
    }))
    hid = created["id"]
    print(f"created harness {hid}  (base={created.get('base')}, default={created.get('defaultModel')})")

    models = hr.check(c.get(f"/v1/harnesses/{hid}/models"))
    avail = [m["id"] for m in models.get("models") or [] if m.get("available")]
    print(f"models it can run now: {', '.join(avail) or '(none: connect a provider)'}")

    # No model given -> the harness's default_model is used.
    resp = hr.check(c.post("/v1/responses", json={
        "input": "What's the difference between a model API and an agent harness?",
        "metadata": {"harness_id": hid},
    }))
    print("\nanswer:", hr.output_text(resp))
    hr.describe(resp)

    print(f"\nPut this in your product config:  HARNESS_ID={hid}")
    print(f"Delete it later:  curl -X DELETE {hr.base_url()}/v1/harnesses/{hid} -H 'Authorization: Bearer ...'")
