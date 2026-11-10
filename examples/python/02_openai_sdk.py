"""Lesson 4: the same task through the official OpenAI Python SDK.

HarnessRouter speaks the OpenAI Responses API, so `client.responses.create` just works.
Only the base URL, the key, and metadata.harness_id differ from a normal OpenAI call.

    python 02_openai_sdk.py --provider anthropic
"""
import hr

a = hr.args(__doc__)
prof = hr.profile(a.provider)
hr.banner("Same task via the OpenAI SDK", prof)

client = hr.openai_client()

resp = client.responses.create(
    model=prof.model,
    input="In one sentence: what is an agent harness?",
    metadata={"harness_id": prof.harness_id},
    # Optional extras HarnessRouter understands:
    # instructions="Answer like a teacher.",       # extra system guidance for this task only
    # reasoning={"effort": "low"},                 # none|minimal|low|medium|high|xhigh
    # extra_body={"max_step": 10, "timeout_seconds": 300},  # UHP budgets (not in the SDK's types)
)

print("answer:", resp.output_text)
# warnings=False: UHP metadata may hold non-string values (e.g. model_fallback: true)
hr.describe(resp.model_dump(warnings=False))
