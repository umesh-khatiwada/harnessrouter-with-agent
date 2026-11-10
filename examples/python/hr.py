"""Shared setup for every example.

Reads the app's .env (repo root), picks a provider profile, and builds HTTP / OpenAI SDK clients
that point at your HarnessRouter instance.

A "profile" is just the pair your product sends on each task:
    metadata.harness_id  -> which agent harness runs (codex, claude-code, hermes, ...)
    model                -> a canonical model id that harness can run on the connected provider
The provider key itself lives inside HarnessRouter, never here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass
class Profile:
    provider: str
    harness_id: str
    model: str
    why: str


# Pairs taken from HarnessRouter's support matrix (docs/support-matrix.md), all passing there.
# Override any of them with HR_<PROVIDER>_HARNESS / HR_<PROVIDER>_MODEL in .env.
DEFAULT_PROFILES = {
    "openai": Profile("openai", "codex", "gpt-5.4-mini",
                      "Codex CLI on an OpenAI connection (its native home)"),
    "anthropic": Profile("anthropic", "claude-code", "claude-sonnet-5",
                         "Claude Code on an Anthropic connection (its native home)"),
    "openrouter": Profile("openrouter", "hermes", "claude-sonnet-4.6",
                          "Hermes on an OpenRouter connection (one key, many vendors)"),
    # NVIDIA is not a built-in provider: it is a "Custom" OpenAI-format integration (see README).
    # Hermes is the one of our three harnesses that speaks that format.
    "nvidia": Profile("nvidia", "hermes", "nemotron-3-ultra",
                      "Hermes on a custom NVIDIA (integrate.api.nvidia.com) integration"),
}


def profile(name: str) -> Profile:
    name = name.lower()
    if name not in DEFAULT_PROFILES:
        sys.exit(f"unknown provider '{name}'. Use one of: {', '.join(DEFAULT_PROFILES)}")
    p = DEFAULT_PROFILES[name]
    up = name.upper()
    return Profile(
        provider=name,
        harness_id=os.getenv(f"HR_{up}_HARNESS", p.harness_id),
        model=os.getenv(f"HR_{up}_MODEL", p.model),
        why=p.why,
    )


def base_url() -> str:
    return os.getenv("HARNESSROUTER_BASE_URL", "http://localhost:3000/api/harness").rstrip("/")


def api_key() -> str:
    key = os.getenv("HARNESSROUTER_API_KEY", "")
    if not key:
        sys.exit("HARNESSROUTER_API_KEY is not set. Copy .env.example to .env and fill it in "
                 "(Console -> API Keys), or use the mock server: see README.")
    return key


def http(timeout: float | None = 3700) -> httpx.Client:
    """An HTTP client for the HarnessRouter API.

    Agent tasks take minutes, so the read timeout is long (task default is 1 hour)."""
    return httpx.Client(
        base_url=base_url(),
        headers={"Authorization": f"Bearer {api_key()}"},
        timeout=httpx.Timeout(timeout, connect=10),
    )


def openai_client():
    """The official OpenAI SDK, pointed at HarnessRouter.

    base_url and api_key are passed explicitly so OPENAI_BASE_URL / OPENAI_API_KEY from your shell
    are never used by accident."""
    from openai import OpenAI

    return OpenAI(base_url=f"{base_url()}/v1", api_key=api_key(), timeout=3700, max_retries=0)


def check(r: httpx.Response) -> dict:
    """Raise a readable error using UHP's error envelope: {"error": {type, code, message}}."""
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or r.json()
        except ValueError:
            err = {"message": r.text[:500]}
        if isinstance(err, dict):
            msg = f"{r.status_code} {err.get('code') or err.get('type') or ''}: {err.get('message') or err}"
        else:
            msg = f"{r.status_code}: {err}"
        raise SystemExit(f"HarnessRouter refused {r.request.method} {r.request.url.path}\n  {msg}")
    return r.json() if r.content else {}


def output_text(resp: dict) -> str:
    """Join the assistant's text from a response object (what the SDK calls .output_text)."""
    parts = []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return "".join(parts)


def artifacts(resp: dict) -> list[dict]:
    """Files the agent produced, cited on the message as container_file_citation annotations."""
    out = []
    for item in resp.get("output") or []:
        for c in item.get("content") or [] if item.get("type") == "message" else []:
            for a in c.get("annotations") or []:
                if a.get("type") == "container_file_citation":
                    out.append(a)
    return out


def describe(resp: dict) -> None:
    """Print the parts of a response you should look at every time."""
    md = resp.get("metadata") or {}
    print(f"  id          : {resp.get('id')}")
    print(f"  status      : {resp.get('status')}")
    print(f"  model ran   : {resp.get('model')}")
    if md.get("model_fallback") or md.get("requested_model"):
        print(f"  !! asked for {md.get('requested_model')} -> fallback: {md.get('model_fallback_reason')}")
    if md.get("ignored_fields"):
        print(f"  ignored     : {md['ignored_fields']}")
    print(f"  session_id  : {md.get('session_id')}")
    if resp.get("usage"):
        u = resp["usage"]
        print(f"  tokens      : in={u.get('input_tokens')} out={u.get('output_tokens')}")
    if resp.get("error"):
        print(f"  error       : {json.dumps(resp['error'])}")
    for a in artifacts(resp):
        print(f"  artifact    : {a.get('filename')} -> {a.get('download_url')}")


def args(description: str, **extra) -> argparse.Namespace:
    """Common CLI: --provider openai|anthropic|openrouter|nvidia (default from HR_PROVIDER or openai)."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--provider", default=os.getenv("HR_PROVIDER", "openai"),
                   choices=list(DEFAULT_PROFILES))
    for flag, kw in extra.items():
        p.add_argument(f"--{flag.replace('_', '-')}", **kw)
    return p.parse_args()


def banner(title: str, prof: Profile) -> None:
    print(f"\n=== {title} ===")
    print(f"provider={prof.provider}  harness_id={prof.harness_id}  model={prof.model}")
    print(f"({prof.why})  ->  {base_url()}\n")
