"""Lesson 9: production behaviour: background tasks, polling, cancel, idempotent retries.

    python 08_background_and_cancel.py --provider openai                 # background + cancel
    python 08_background_and_cancel.py --provider openai --idempotency   # safe retry demo
    python 08_background_and_cancel.py --get resp_...                    # re-read any response
"""
import time
import uuid

import hr

a = hr.args(__doc__,
            idempotency={"action": "store_true", "help": "show that a retried request runs once"},
            cancel_after={"type": float, "default": 8.0, "help": "seconds before cancelling"},
            get={"default": None, "help": "just print GET /v1/responses/{id}"})
prof = hr.profile(a.provider)

with hr.http() as c:
    if a.get:
        resp = hr.check(c.get(f"/v1/responses/{a.get}"))
        print("answer:", hr.output_text(resp))
        hr.describe(resp)
        raise SystemExit(0)

    if a.idempotency:
        hr.banner("Idempotent retry", prof)
        key = str(uuid.uuid4())
        body = {"input": "Reply with exactly: once.", "model": prof.model,
                "metadata": {"harness_id": prof.harness_id}}
        ids = []
        for attempt in (1, 2):  # pretend attempt 1 timed out and we retried
            r = hr.check(c.post("/v1/responses", json=body, headers={"Idempotency-Key": key}))
            ids.append(r["id"])
            print(f"attempt {attempt}: {r['id']} status={r.get('status')}")
        print(f"\nsame response both times: {ids[0] == ids[1]}  (the task ran once)")
        raise SystemExit(0)

    hr.banner("Background task + cancel", prof)
    # background: the POST returns at once with status in_progress; nothing holds a connection.
    resp = hr.check(c.post("/v1/responses", json={
        "input": "Count slowly from 1 to 200, running `sleep 1` in the shell between each number.",
        "model": prof.model,
        "metadata": {"harness_id": prof.harness_id},
        "background": True,
    }))
    rid = resp["id"]
    print(f"started {rid} status={resp.get('status')}")

    deadline = time.monotonic() + a.cancel_after
    while time.monotonic() < deadline:
        time.sleep(2)
        resp = hr.check(c.get(f"/v1/responses/{rid}"))
        print(f"  poll: status={resp.get('status')}")
        if resp.get("status") not in ("queued", "in_progress"):
            break

    if resp.get("status") in ("queued", "in_progress"):
        print(f"\ncancelling after {a.cancel_after:.0f}s ...")
        hr.check(c.post(f"/v1/responses/{rid}/cancel"))
        hr.check(c.post(f"/v1/responses/{rid}/cancel"))  # twice is fine: cancel is idempotent
        for _ in range(30):
            resp = hr.check(c.get(f"/v1/responses/{rid}"))
            if resp.get("status") not in ("queued", "in_progress"):
                break
            time.sleep(1)

    print()
    hr.describe(resp)
    print("\nThe session is still usable: continue it with previous_response_id =", rid)
