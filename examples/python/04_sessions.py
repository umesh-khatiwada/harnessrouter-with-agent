"""Lesson 6: continue a session with previous_response_id.

Turn 1 writes a file and states a codeword. Turn 2 continues the same session (same workspace,
same conversation) without resending any history. Then we read the session's turns back.

    python 04_sessions.py --provider anthropic
    python 04_sessions.py --provider openai --switch-model gpt-5.5   # change model mid-session
"""
import hr

a = hr.args(__doc__, switch_model={"default": None, "help": "model for turn 2 (optional)"})
prof = hr.profile(a.provider)
hr.banner("Sessions", prof)

with hr.http() as c:
    t1 = hr.check(c.post("/v1/responses", json={
        "input": "The codeword is PELICAN. Create a file notes.md containing one line: 'turn 1 was here'. "
                 "Reply with just: saved.",
        "model": prof.model,
        "metadata": {"harness_id": prof.harness_id},
    }))
    print("turn 1:", hr.output_text(t1))
    hr.describe(t1)
    session_id = (t1.get("metadata") or {}).get("session_id")

    t2 = hr.check(c.post("/v1/responses", json={
        "input": "What was the codeword? Also append 'turn 2 was here' to notes.md and show me the file.",
        "previous_response_id": t1["id"],      # <- continue, don't resend history
        "model": a.switch_model or prof.model,  # switching model mid-session is allowed
        # harness_id may be omitted; if sent it must match the session's harness (else 409)
        "metadata": {"harness_id": prof.harness_id},
    }))
    print("\nturn 2:", hr.output_text(t2))
    hr.describe(t2)
    same = (t2.get("metadata") or {}).get("session_id") == session_id
    print(f"\nsame session: {same}")

    if session_id:
        turns = hr.check(c.get(f"/v1/sessions/{session_id}/turns"))
        items = turns.get("turns") or turns.get("data") or []
        print(f"GET /v1/sessions/{session_id}/turns -> {len(items)} turn(s)")
