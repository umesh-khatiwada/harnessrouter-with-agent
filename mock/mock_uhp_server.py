"""A tiny offline stand-in for HarnessRouter, for learning the API shape without Docker or keys.

    python mock/mock_uhp_server.py            # listens on http://localhost:8787
    # then in .env:  HARNESSROUTER_BASE_URL=http://localhost:8787  HARNESSROUTER_API_KEY=mock-key
    # (the only key it accepts; change it with MOCK_API_KEY=...)

It is NOT HarnessRouter and runs no model. It answers the same endpoints with the same JSON and
SSE shapes (UHP 2026-10-19), and fakes an "agent" with a few canned behaviours so every example
script has something to show: exact replies, a remembered codeword, writing files, a CSV report,
and a slow countdown you can cancel. Python standard library only.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8787
MOCK_KEY = os.environ.get("MOCK_API_KEY", "mock-key")

# base id -> (backend, models it can run, default model)
BASES = {
    "codex": ("codex", ["gpt-5.4-mini", "gpt-5.5"], "gpt-5.4-mini"),
    "claude-code": ("claude", ["claude-sonnet-5", "claude-haiku-4.5", "claude-opus-5"], "claude-sonnet-5"),
    "hermes": ("hermes", ["claude-sonnet-4.6", "gpt-5.4-mini", "nemotron-3-ultra"], "claude-sonnet-4.6"),
}

LOCK = threading.RLock()
HARNESSES: dict[str, dict] = {}      # chrn_ id -> harness record
RESPONSES: dict[str, dict] = {}      # resp_ id -> response object
CANCEL: dict[str, threading.Event] = {}
SESSIONS: dict[str, dict] = {}       # hsess id -> {harness_id, turns, files{path: bytes}, ids{path: fid}, busy}
UPLOADS: dict[str, dict] = {}        # file_ id -> {filename, data, media_type}
IDEMPOTENT: dict[tuple, tuple] = {}  # (scope, key) -> (body hash, object id)


def rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def err(status: int, code: str, message: str, param: str | None = None, etype: str | None = None):
    etype = etype or ("authentication_error" if status == 401 else
                      "server_error" if status >= 500 else "invalid_request_error")
    return status, {"error": {"type": etype, "code": code, "message": message, "param": param, "detail": None}}


def harness_view(h: dict) -> dict:
    return {"id": h["id"], "object": "harness", "name": h["name"], "base": h["base"],
            "defaultModel": h["default_model"], "systemPrompt": h.get("system_prompt", ""),
            "mcpServers": [], "skills": [], "plugins": [], "disabledTools": [],
            "maxStep": h.get("max_step"), "timeoutSeconds": h.get("timeout_seconds"),
            "createdAt": h["created_at"]}


def resolve_harness(hid: str | None) -> dict | None:
    """A built-in base id works as a harness id, like on HarnessRouter."""
    hid = hid or "codex"
    if hid in BASES:
        backend, models, default = BASES[hid]
        return {"id": hid, "base": hid, "default_model": default, "system_prompt": ""}
    return HARNESSES.get(hid)


# ── the fake agent ────────────────────────────────────────────────────────────────────────────

def fake_agent(text: str, sess: dict, cancel: threading.Event, emit_tool) -> tuple[str, list[str]]:
    """Return (final text, paths written). Calls emit_tool(name, args, output) for each 'tool'."""
    written: list[str] = []
    low = text.lower()

    if m := re.search(r"codeword(?:\s+is|:)\s*([A-Z][A-Z0-9_]+)", text):
        sess["codeword"] = m.group(1)

    if "count slowly" in low:
        for i in range(1, 201):
            if cancel.wait(1.0):
                return f"Stopped at {i - 1}.", written
            emit_tool("shell", {"command": f"echo {i} && sleep 1"}, str(i))
        return "Counted to 200.", written

    def write(path: str, content: str):
        sess["files"][path] = content.encode()
        written.append(path)
        emit_tool("shell", {"command": f"cat > {path} <<'EOF' ... EOF"}, f"wrote {len(content)} bytes")

    reply = None
    csvs = [p for p in sess["files"] if p.endswith(".csv")]
    if csvs and "report.md" in low:
        rows = list(csv.DictReader(io.StringIO(sess["files"][csvs[0]].decode())))
        emit_tool("shell", {"command": f"head -5 {csvs[0]}"}, "\n".join(sess["files"][csvs[0]].decode().splitlines()[:5]))
        totals: dict[str, float] = {}
        for r in rows:
            try:
                totals[r["region"]] = totals.get(r["region"], 0) + float(r["units"]) * float(r["unit_price"])
            except (KeyError, ValueError):
                pass
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        lines = ["# Revenue by region", "", "| Region | Revenue |", "|---|---|"]
        lines += [f"| {k} | {v:,.2f} |" for k, v in ranked]
        if ranked:
            lines += ["", f"{ranked[0][0]} leads with {ranked[0][1] / sum(totals.values()):.0%} of revenue."]
        write("report.md", "\n".join(lines) + "\n")
        reply = ranked[0][0] if ranked else "No data."
    else:
        for m in re.finditer(r"(create|write|append)[^.]*?\b([\w\-/]+\.(?:md|py|txt|json))", text, re.I):
            verb, path = m.group(1).lower(), m.group(2)
            q = re.search(r"'([^']+)'", text[max(0, m.start() - 60):m.end() + 80])
            line = q.group(1) if q else f"written by the mock agent ({verb})"
            if path.endswith(".py") and "print" in low:
                pm = re.search(r"prints? '([^']+)'", text)
                line = f"print({(pm.group(1) if pm else 'hello')!r})"
            old = sess["files"].get(path, b"").decode() if verb == "append" else ""
            write(path, old + line + "\n")
            if path.endswith(".py"):
                out = re.search(r"print\('(.*)'\)", line)
                printed = out.group(1) if out else ""
                emit_tool("shell", {"command": f"python {path}"}, printed)
                reply = f"I created {path} and ran it. It printed: {printed}"

    if reply is None:
        if m := re.search(r"reply with (?:exactly|just|only)(?: this and nothing else)?:\s*(.+?)\s*$", text, re.I | re.M):
            reply = m.group(1)
        elif "what was the codeword" in low:
            reply = f"The codeword was {sess.get('codeword', '(none given)')}."
        elif "fibonacci" in low:
            emit_tool("shell", {"command": "python -c 'f=lambda n:n if n<2 else f(n-1)+f(n-2);print(f(10))'"}, "55")
            reply = "55"
        else:
            reply = f"(mock agent) I received: {text[:120]}"
    if written and "show me the file" in low:
        reply += "\n\n" + "\n".join(f"{p}:\n{sess['files'][p].decode()}" for p in written)
    return reply, written


# ── running a task ───────────────────────────────────────────────────────────────────────────

def input_text_and_files(inp, sess: dict) -> str:
    if isinstance(inp, str):
        return inp
    texts = []
    for item in inp or []:
        if item.get("type") == "input_response":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "input_text":
                texts.append(c.get("text", ""))
            elif c.get("type") == "input_file":
                if c.get("file_id") and c["file_id"] in UPLOADS:
                    up = UPLOADS[c["file_id"]]
                    sess["files"][c.get("filename") or up["filename"]] = up["data"]
                elif c.get("file_data", "").startswith("data:"):
                    sess["files"][c.get("filename", "upload.bin")] = base64.b64decode(c["file_data"].split(",", 1)[1])
    return "\n".join(texts)


def run_task(resp: dict, body: dict, sess: dict, emit) -> None:
    """Fill resp in place, calling emit(event_dict) for each SSE event."""
    cancel = CANCEL[resp["id"]]
    out = resp["output"]

    emit({"type": "response.created", "response": dict(resp)})
    emit({"type": "response.in_progress", "response": dict(resp)})

    def emit_tool(name, args, output):
        call_id = rid("call")
        item = {"id": rid("fc"), "type": "function_call", "call_id": call_id, "name": name,
                "arguments": json.dumps(args), "status": "completed"}
        idx = len(out)
        out.append(item)
        emit({"type": "response.output_item.added", "output_index": idx, "item": {**item, "arguments": ""}})
        emit({"type": "response.function_call_arguments.done", "output_index": idx, "item_id": item["id"],
              "arguments": item["arguments"]})
        emit({"type": "response.output_item.done", "output_index": idx, "item": item})
        res = {"id": rid("fco"), "type": "function_call_output", "call_id": call_id, "output": output}
        out.append(res)
        emit({"type": "response.output_item.added", "output_index": idx + 1, "item": res})
        emit({"type": "response.output_item.done", "output_index": idx + 1, "item": res})

    text = input_text_and_files(body.get("input"), sess)
    reply, written = fake_agent(text, sess, cancel, emit_tool)
    sess["history"].append(text)

    annotations = []
    for p in written:
        fid = sess["ids"].setdefault(p, rid("file"))
        annotations.append({"type": "container_file_citation", "container_id": resp["metadata"]["session_id"],
                            "file_id": fid, "filename": p.rsplit("/", 1)[-1],
                            "download_url": f"http://localhost:{PORT}/v1/containers/{resp['metadata']['session_id']}/files/{fid}/content",
                            "start_index": 0, "end_index": 0})
    sess["changed"] = written

    msg = {"id": rid("msg"), "type": "message", "role": "assistant", "status": "in_progress", "content": []}
    idx = len(out)
    out.append(msg)
    emit({"type": "response.output_item.added", "output_index": idx, "item": dict(msg, content=[])})
    emit({"type": "response.content_part.added", "item_id": msg["id"], "output_index": idx, "content_index": 0,
          "part": {"type": "output_text", "text": "", "annotations": []}})
    for chunk in re.findall(r"\S+\s*|\s+", reply):
        emit({"type": "response.output_text.delta", "item_id": msg["id"], "output_index": idx,
              "content_index": 0, "delta": chunk})
        time.sleep(0.02)
    part = {"type": "output_text", "text": reply, "annotations": annotations}
    msg["content"] = [part]
    msg["status"] = "completed"
    emit({"type": "response.output_text.done", "item_id": msg["id"], "output_index": idx, "content_index": 0, "text": reply})
    emit({"type": "response.content_part.done", "item_id": msg["id"], "output_index": idx, "content_index": 0, "part": part})
    emit({"type": "response.output_item.done", "output_index": idx, "item": msg})

    resp["usage"] = {"input_tokens": 9000 + len(text) // 4, "output_tokens": len(reply) // 4 + 5,
                     "total_tokens": 9000 + len(text) // 4 + len(reply) // 4 + 5}
    resp["status"] = "cancelled" if cancel.is_set() else "completed"
    sess["busy"] = False
    emit({"type": "response.failed" if resp["status"] == "cancelled" else "response.completed", "response": dict(resp)})


def new_response(body: dict) -> tuple[int, dict] | tuple[dict, dict]:
    """Validate and create the response + session. Returns (resp, session) or (status, error)."""
    md = dict(body.get("metadata") or {})
    prev = body.get("previous_response_id")
    if prev:
        if prev not in RESPONSES:
            return err(404, "response_not_found", f"No response with id '{prev}'.", "previous_response_id")
        sid = RESPONSES[prev]["metadata"]["session_id"]
        sess = SESSIONS[sid]
        if md.get("harness_id") and md["harness_id"] != sess["harness_id"]:
            return err(409, "harness_mismatch", "The task named a different harness than the session it continues.",
                       "metadata.harness_id")
        if sess["busy"]:
            return err(409, "session_busy", "A task is already running in this session.")
        h = resolve_harness(sess["harness_id"])
    else:
        h = resolve_harness(md.get("harness_id"))
        if h is None:
            return err(404, "harness_not_found", f"No harness with id '{md.get('harness_id')}'.", "metadata.harness_id")
        sid = "hsess" + uuid.uuid4().hex
        sess = SESSIONS[sid] = {"harness_id": h["id"], "turns": [], "files": {}, "ids": {}, "busy": False,
                                "history": [], "changed": []}
    backend, models, _ = BASES[h["base"]]
    model = body.get("model") or h["default_model"]
    if model not in models:
        return err(422, "model_unavailable",
                   f"model '{model}' is not available for this harness's backend ({backend}); try one of {models}", "model")
    ignored = [f for f in ("tools", "include") if f in body]
    resp = {"id": rid("resp"), "object": "response", "created_at": int(time.time()), "status": "in_progress",
            "error": None, "incomplete_details": None, "previous_response_id": prev, "model": model,
            "output": [], "store": body.get("store", True), "usage": None,
            "connection": "mock", "served_model": model,
            "metadata": {**md, "session_id": sid, "harness_id": h["id"], "mock": True,
                         **({"ignored_fields": ignored} if ignored else {})}}
    sess["busy"] = True
    sess["turns"].append(resp["id"])
    RESPONSES[resp["id"]] = resp
    CANCEL[resp["id"]] = threading.Event()
    return resp, sess


# ── HTTP layer ───────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "mock-uhp/0.1"

    def log_message(self, fmt, *args):  # quieter log
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def send_json(self, status: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            self.send_json(*err(401, "missing_credential", "No bearer token."))
            return False
        if auth[7:].strip() != MOCK_KEY:  # like the real server: a wrong key is refused
            self.send_json(*err(401, "invalid_credential", "missing or invalid API key"))
            return False
        return True

    # GET ----------------------------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/v1/openapi.json",):
            return self.send_json(200, {"openapi": "3.1.0", "info": {"title": "mock UHP server"}})
        if not self.authed():
            return
        with LOCK:
            if p == "/v1/harnesses":
                return self.send_json(200, {"harnesses": [harness_view(h) for h in HARNESSES.values()]})
            if p == "/v1/bases":
                return self.send_json(200, {"bases": [
                    {"id": b, "object": "harness.base", "backend": be, "status": "ready", "defaultModel": d,
                     "models": [{"id": m, "available": True, "default": m == d} for m in ms]}
                    for b, (be, ms, d) in BASES.items()]})
            if p == "/v1/models":
                return self.send_json(200, {"backends": {be: {"default": d, "models": [
                    {"id": m, "backend": be, "available": True, "default": m == d} for m in ms]}
                    for _, (be, ms, d) in BASES.items()}})
            if m := re.fullmatch(r"/v1/harnesses/([^/]+)/models", p):
                h = resolve_harness(m.group(1))
                if not h:
                    return self.send_json(*err(404, "harness_not_found", "No such harness."))
                be, ms, _ = BASES[h["base"]]
                return self.send_json(200, {"harness_id": h["id"], "backend": be, "default": h["default_model"],
                                            "models": [{"id": x, "available": True, "default": x == h["default_model"]} for x in ms]})
            if m := re.fullmatch(r"/v1/harnesses/([^/]+)", p):
                h = HARNESSES.get(m.group(1))
                return self.send_json(200, harness_view(h)) if h else self.send_json(*err(404, "harness_not_found", "No such harness."))
            if m := re.fullmatch(r"/v1/responses/([^/]+)", p):
                r = RESPONSES.get(m.group(1))
                return self.send_json(200, r) if r else self.send_json(*err(404, "response_not_found", "No such response."))
            if m := re.fullmatch(r"/v1/sessions/([^/]+)/turns", p):
                s = SESSIONS.get(m.group(1))
                if not s:
                    return self.send_json(*err(404, "session_not_found", "No such session."))
                return self.send_json(200, {"session_id": m.group(1), "turns": [RESPONSES[t] for t in s["turns"]]})
            if m := re.fullmatch(r"/v1/sessions/([^/]+)/files", p):
                sid = m.group(1)
                s = SESSIONS.get(sid)
                if not s:
                    return self.send_json(*err(404, "session_not_found", "No such session."))
                changed = parse_qs(u.query).get("changed", ["false"])[0] == "true"
                paths = s["changed"] if changed else sorted(s["files"])
                return self.send_json(200, {"files": [
                    {"object": "file", "id": s["ids"].setdefault(x, rid("file")), "file_id": s["ids"][x],
                     "container_id": sid, "filename": x.rsplit("/", 1)[-1], "path": x, "bytes": len(s["files"][x]),
                     "download_url": f"/v1/containers/{sid}/files/{s['ids'][x]}/content"} for x in paths]})
            if m := re.fullmatch(r"/v1/containers/([^/]+)/files/([^/]+)/content", p):
                s = SESSIONS.get(m.group(1))
                path = next((k for k, v in (s or {}).get("ids", {}).items() if v == m.group(2)), None)
                if not path:
                    return self.send_json(*err(404, "file_not_found", "No such file."))
                data = s["files"][path]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{path.rsplit("/", 1)[-1]}"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
        self.send_json(*err(404, "not_found", f"The mock does not implement GET {p}."))

    # DELETE -------------------------------------------------------------------------------
    def do_DELETE(self):
        p = urlparse(self.path).path
        if not self.authed():
            return
        with LOCK:
            if m := re.fullmatch(r"/v1/harnesses/([^/]+)", p):
                if HARNESSES.pop(m.group(1), None) is None:
                    return self.send_json(*err(404, "harness_not_found", "No such harness."))
                return self.send_json(200, {"id": m.group(1), "deleted": True})
        self.send_json(*err(404, "not_found", f"The mock does not implement DELETE {p}."))

    # POST ---------------------------------------------------------------------------------
    def do_POST(self):
        p = urlparse(self.path).path
        if not self.authed():
            return
        raw = self.body()

        if p == "/v1/files":
            ctype = self.headers.get("Content-Type", "")
            msg = BytesParser(policy=email_policy).parsebytes(
                f"Content-Type: {ctype}\r\n\r\n".encode() + raw)
            for part in msg.iter_parts():
                if part.get_param("name", header="content-disposition") == "file":
                    fid = rid("file")
                    data = part.get_payload(decode=True) or b""
                    UPLOADS[fid] = {"filename": part.get_filename() or "upload.bin", "data": data}
                    return self.send_json(200, {"id": fid, "object": "file", "filename": UPLOADS[fid]["filename"],
                                                "bytes": len(data), "created_at": int(time.time()), "purpose": "user_data"})
            return self.send_json(*err(400, "invalid_input", "multipart field 'file' is required.", "file"))

        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.send_json(*err(400, "invalid_input", "Body is not JSON."))

        if m := re.fullmatch(r"/v1/responses/([^/]+)/cancel", p):
            r = RESPONSES.get(m.group(1))
            if not r:
                return self.send_json(*err(404, "response_not_found", "No such response."))
            CANCEL[r["id"]].set()  # idempotent: cancelling a finished task changes nothing
            return self.send_json(200, r)

        if p == "/v1/harnesses":
            key = self.headers.get("Idempotency-Key")
            h_hash = hashlib.sha256(raw).hexdigest()
            with LOCK:
                if key and ("h", key) in IDEMPOTENT:
                    old_hash, hid = IDEMPOTENT[("h", key)]
                    if old_hash != h_hash:
                        return self.send_json(*err(409, "conflict", "Idempotency-Key reused with a different request payload"))
                    return self.send_json(200, harness_view(HARNESSES[hid]))
                if body.get("base") not in BASES:
                    return self.send_json(*err(422, "unsupported_base", f"Unsupported base '{body.get('base')}'.", "base"))
                _, models, default = BASES[body["base"]]
                dm = body.get("default_model") or default
                if dm not in models:
                    return self.send_json(*err(422, "model_not_available", f"'{dm}' is not run by {body['base']}.", "default_model"))
                h = {"id": rid("chrn"), "name": body.get("name") or "Untitled", "base": body["base"],
                     "default_model": dm, "system_prompt": body.get("system_prompt", ""),
                     "max_step": body.get("max_step"), "timeout_seconds": body.get("timeout_seconds"),
                     "created_at": int(time.time() * 1000)}
                HARNESSES[h["id"]] = h
                if key:
                    IDEMPOTENT[("h", key)] = (h_hash, h["id"])
            return self.send_json(200, harness_view(h))

        if p == "/v1/responses":
            return self.create_response(body)

        self.send_json(*err(404, "not_found", f"The mock does not implement POST {p}."))

    def create_response(self, body: dict):
        key = self.headers.get("Idempotency-Key")
        # `stream` is not part of the request's identity (same as HarnessRouter)
        b_hash = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "stream"},
                                           sort_keys=True).encode()).hexdigest()
        with LOCK:
            if key and ("r", key) in IDEMPOTENT:
                old_hash, resp_id = IDEMPOTENT[("r", key)]
                if old_hash != b_hash:
                    return self.send_json(*err(409, "conflict", "Idempotency-Key reused with a different request payload"))
                while RESPONSES[resp_id]["status"] == "in_progress":  # wait for the first one
                    LOCK.release()
                    time.sleep(0.1)
                    LOCK.acquire()
                return self.send_json(200, RESPONSES[resp_id])
            res = new_response(body)
            if isinstance(res[0], int):
                return self.send_json(*res)
            resp, sess = res
            if key:
                IDEMPOTENT[("r", key)] = (b_hash, resp["id"])

        if body.get("background"):
            threading.Thread(target=run_task, args=(resp, body, sess, lambda e: None), daemon=True).start()
            return self.send_json(200, dict(resp))

        if not body.get("stream"):
            run_task(resp, body, sess, lambda e: None)
            return self.send_json(200, resp)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        seq = [0]
        alive = [True]

        def emit(ev):
            if not alive[0]:
                return  # client went away; the task keeps running (Streaming §5)
            ev = {"type": ev["type"], "sequence_number": seq[0], **{k: v for k, v in ev.items() if k != "type"}}
            seq[0] += 1
            try:
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                alive[0] = False

        run_task(resp, body, sess, emit)
        self.close_connection = True


if __name__ == "__main__":
    print(f"mock UHP server on http://localhost:{PORT}  (any bearer token works)")
    print("This is a teaching stub, not HarnessRouter: no model runs.\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
