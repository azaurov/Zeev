#!/usr/bin/env python3
"""Benchmark free/cheap LLM providers on multi-step file-tool calling.

Decides which providers may join the web agent loop's fallback chain
(zeev.py `_AGENT_CHAIN`). Non-streaming on purpose: tool calls only arrive
complete, so the agent loop never streams them. Each trial builds a fresh
throwaway workspace with random code words (some gateways cache identical
requests, so a fixed prompt would measure the cache).

    python3 scripts/agent_tool_probe.py --only groq,cloudflare --trials 2

Pass = the conversation finished within MAX_STEPS, every tool call had a known
name and parseable JSON arguments, the final answer contains the planted fact,
and no raw tool-call markup or <think> block leaked into the visible reply.
Prints key NAMES only, never values.
"""
import argparse
import json
import os
import random
import re
import string
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zeev"))
import agent_fs  # noqa: E402
import zeev      # noqa: E402

MAX_STEPS = 5
SYSTEM = ("You are Zeev, a helpful assistant. The user's workspace folder is reachable "
          "only through the provided tools; use them, never guess file contents. "
          "Answer in one or two short sentences once you have the facts.")


def candidates():
    z = zeev
    out = []
    if z.GROQ_API_KEY:
        out.append(("groq", z.GROQ_URL, z.GROQ_API_KEY, z.MODELS["2"][0], {"reasoning_effort": "none"}))
    if z.CLOUDFLARE_AI_URL:
        for m in z._CLOUDFLARE_CHAT_CANDIDATES:
            out.append(("cloudflare", z.CLOUDFLARE_AI_URL, z.CLOUDFLARE_API_KEY, m, {}))
    if z.REQUESTY_API_KEY:
        for m in z._REQUESTY_FREE_CANDIDATES:
            out.append(("requesty", z.REQUESTY_URL, z.REQUESTY_API_KEY, m, {}))
    if z.OPENROUTER_API_KEY:
        for m in z._OPENROUTER_FREE_CANDIDATES:
            out.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                        z.OPENROUTER_API_KEY, m, {}))
    if z.FREEAI_API_KEY:
        for m in z._FREEAI_CHAT_CANDIDATES:
            out.append(("freeai", z.FREEAI_URL, z.FREEAI_API_KEY, m, {}))
    return out


def rnd(n=6):
    return "".join(random.choices(string.ascii_lowercase, k=n))


def make_workspace(root):
    """Returns [(prompt, expected_substring)] with facts unique to this trial."""
    word, num = rnd(), str(random.randint(1000, 9999))
    (root / "projects").mkdir()
    (root / "projects" / f"{rnd()}.txt").write_text(f"filler {rnd()}\n")
    (root / "projects" / "garden.txt").write_text(
        f"Garden plan\nproject codeword: {word}\nbudget: {num} dollars\n")
    (root / "todo.txt").write_text("buy milk\ncall the plumber\n")
    return [
        (f"Which file in my workspace mentions '{word}', and what budget does it list?", num),
        ("Look inside my projects folder and tell me the budget in the garden plan.", num),
    ]


def post(url, key, model, extra, msgs):
    payload = {"model": model, "messages": msgs, "temperature": 0.3, "max_tokens": 700,
               "stream": False, "tools": agent_fs.AGENT_TOOLS, "tool_choice": "auto", **extra}
    t0 = time.time()
    r = requests.post(url, json=payload, timeout=90,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    return r, time.time() - t0


def run_trial(cand, prompt, expect):
    label, url, key, model, extra = cand
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    calls_made, elapsed = [], 0.0
    for step in range(MAX_STEPS):
        try:
            r, dt = post(url, key, model, extra, msgs)
        except requests.RequestException as e:
            return False, f"network: {e}", calls_made, elapsed
        elapsed += dt
        if r.status_code != 200:
            return False, f"http {r.status_code}: {r.text[:120]!r}", calls_made, elapsed
        try:
            msg = r.json()["choices"][0]["message"]
        except Exception as e:
            return False, f"bad body: {e}", calls_made, elapsed
        tcs = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if not tcs:
            if re.search(r"<think>|<tool_call>|\"name\"\s*:\s*\"(list_dir|read_file|search_files)", content):
                return False, f"leaked markup: {content[:100]!r}", calls_made, elapsed
            if not calls_made:
                return False, "answered without using any tool", calls_made, elapsed
            ok = expect in content.replace(",", "")
            return ok, ("ok" if ok else f"wrong answer: {content[:120]!r}"), calls_made, elapsed
        msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")}
                    | {"content": content})
        for tc in tcs:
            fn = tc.get("function") or {}
            name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
            if name not in agent_fs.AGENT_TOOL_NAMES:
                return False, f"unknown tool {name!r}", calls_made, elapsed
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                assert isinstance(args, dict)
            except Exception:
                return False, f"bad args {raw!r}", calls_made, elapsed
            calls_made.append(name)
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", f"c{step}"),
                         "name": name, "content": agent_fs.run_agent_tool(name, args)})
    return False, f"no final answer in {MAX_STEPS} steps", calls_made, elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list of providers")
    ap.add_argument("--models", default="", help="substring filter on model id")
    ap.add_argument("--trials", type=int, default=2)
    a = ap.parse_args()
    only = {s for s in a.only.split(",") if s}
    print("keys present:", {k: bool(getattr(zeev, k, "")) for k in
          ("GROQ_API_KEY", "CLOUDFLARE_API_KEY", "REQUESTY_API_KEY", "OPENROUTER_API_KEY", "FREEAI_API_KEY")})
    for cand in candidates():
        if only and cand[0] not in only:
            continue
        if a.models and a.models not in cand[3]:
            continue
        results = []
        for t in range(a.trials):
            with tempfile.TemporaryDirectory() as d:
                os.environ["ZEEV_AGENT_ROOT"] = d
                for prompt, expect in make_workspace(Path(d)):
                    results.append(run_trial(cand, prompt, expect))
                    time.sleep(1.5)
        passed = sum(1 for r in results if r[0])
        avg = sum(r[3] for r in results) / max(1, len(results))
        print(f"\n== {cand[0]} / {cand[3]}: {passed}/{len(results)} passed, avg {avg:.1f}s/conversation")
        for ok, why, calls, dt in results:
            print(f"   {'PASS' if ok else 'FAIL'} {dt:5.1f}s tools={calls} {why if not ok else ''}")


if __name__ == "__main__":
    main()
