"""Real MiniMax-M3 prompt-cache baseline (REAL INTEGRATION — opt-in only).

This script calls the live MiniMax-M3 endpoint over HTTP and is therefore a
*real integration* test in the sense of AGENTS.md / CONVENTIONS. It is NOT run
by the normal regression suite and is not a Stage-1 release gate.

Purpose: produce a one-time cache baseline. Within five minutes it runs two
turns in the SAME session keeping a >=512 token stable prefix, and prints the
two model_usage observations so cache_read_input_tokens can be eyeballed across
turns. If the second turn shows zero cache read it only records diagnostics; it
does not fail.

Run it explicitly, pointing at a live server with real MINIMAX_* env set:

    cd backend
    # terminal 1: start the real server
    uvicorn dsagents.api:app --port 8000
    # terminal 2:
    python -m tests.test_minimax_cache_baseline

The script never imports app code that opens DBs; it only talks to BASE_URL.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("DSAGENTS_BASE_URL", "http://127.0.0.1:8000")
# >=512 tokens of stable prefix, repeated so the provider has enough cacheable
# content. Kept identical across both turns so the prefix is byte-stable.
STABLE_PREFIX = (
    "You are auditing document extraction quality. " * 40
)
TIMEOUT_SECONDS = 120


def run() -> None:
    session = _post_runs_start_session()
    turn1 = _run_turn(session, "Turn 1: summarize the stable context in one short sentence.")
    turn2 = _run_turn(session, "Turn 2: now name one risk in that context in one short sentence.")
    _report(session, turn1, turn2)


def _post_runs_start_session() -> str:
    # The first POST just establishes a session_id we reuse for both turns.
    body = _json_body([_user(STABLE_PREFIX + "\n\nBegin.")])
    payload = _post_json(f"{BASE_URL}/runs", body)
    return payload["session_id"]


def _run_turn(session_id: str, prompt: str) -> dict:
    body = _json_body([_user(prompt)])
    body["session_id"] = session_id
    started = _post_json(f"{BASE_URL}/runs", body)
    run_id = started["run_id"]
    payload = _poll(run_id)
    usage = payload.get("usage")
    if usage is None:
        print(f"[{prompt[:40]}...] run {run_id} returned no usage", file=sys.stderr)
        return {"run_id": run_id, "usage": None}
    return {"run_id": run_id, "usage": usage}


def _poll(run_id: str, deadline_s: float = 300.0) -> dict:
    start = time.time()
    while time.time() - start < deadline_s:
        payload = _get_json(f"{BASE_URL}/runs/{run_id}")
        status = payload["run"]["status"]
        if status in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"run {run_id} never finished within {deadline_s}s")


def _report(session: str, turn1: dict, turn2: dict) -> None:
    print(f"session: {session}")
    for label, turn in (("turn 1", turn1), ("turn 2", turn2)):
        usage = turn.get("usage")
        if not usage:
            print(f"{label}: no usage recorded")
            continue
        print(
            f"{label}: model_calls={usage['model_calls']} "
            f"input={usage['input_tokens']} output={usage['output_tokens']} "
            f"cache_read={usage['cache_read_input_tokens']} "
            f"cache_creation={usage['cache_creation_input_tokens']} "
            f"hit_rate={usage['cache_hit_rate']} "
            f"est_cost_cny={usage['estimated_cost_cny']}"
        )
    read1 = _cache_read(turn1)
    read2 = _cache_read(turn2)
    if read2 == 0:
        print(
            "\nDIAGNOSTIC: second turn cache_read was 0. This is recorded, not a "
            "failure. Possible causes: prefix changed between turns, the "
            "provider did not retain a 5m cache entry, or the endpoint did not "
            "emit cache token fields. Final billing is whatever MiniMax invoices."
        )
    else:
        print(f"\nBaseline established: turn2 cache_read={read2} (turn1={read1}).")


def _cache_read(turn: dict) -> int:
    usage = turn.get("usage") or {}
    return int(usage.get("cache_read_input_tokens") or 0)


def _user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _json_body(messages: list[dict]) -> dict:
    return {"messages": messages}


def _post_json(url: str, body: dict) -> dict:
    data = _to_json(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return _from_json(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return _from_json(resp.read().decode("utf-8"))


def _to_json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _from_json(text: str) -> dict:
    import json

    return json.loads(text)


if __name__ == "__main__":
    if not os.getenv("MINIMAX_API_KEY"):
        # Guard against an accidental run against a server with no real key;
        # the server itself reads MINIMAX_*, so an empty key here usually means
        # the operator forgot to source the real env before starting it.
        print(
            "MINIMAX_API_KEY is not set in this shell. Confirm the live server "
            "was started with real MINIMAX_* env before proceeding.",
            file=sys.stderr,
        )
    try:
        run()
    except (urllib.error.URLError, ConnectionError) as exc:
        sys.exit(
            f"Could not reach {BASE_URL} — start the real server first "
            f"(e.g. `uvicorn dsagents.api:app --port 8000`). Underlying error: {exc}"
        )
