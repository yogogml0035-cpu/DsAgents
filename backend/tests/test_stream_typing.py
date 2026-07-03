from __future__ import annotations

import argparse
import json
import sys
import time

import requests


DEFAULT_URL = "http://127.0.0.1:8500/sessions/messages/stream"


def main() -> None:
    args = parse_args()
    response = requests.post(
        DEFAULT_URL,
        json={"message": args.message, "session_id": args.session_id},
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=(10, 300),
    )
    response.raise_for_status()

    event_name: str | None = None
    data_lines: list[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line:
            if event_name and data_lines:
                handle_event(event_name, "\n".join(data_lines), args.char_delay)
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream /sessions/messages/stream and print text like a typewriter.")
    parser.add_argument("message", nargs="?", default="你是谁", help="Message sent to the local SSE endpoint.")
    parser.add_argument("--session-id", dest="session_id", default="4ffcc065a3334258af6a532a85733c50", help="Optional session_id to continue a session.")
    parser.add_argument("--char-delay", dest="char_delay", type=float, default=0.01, help="Delay between printed characters.")
    return parser.parse_args()


def handle_event(event_name: str, raw_data: str, char_delay: float) -> None:
    payload = json.loads(raw_data)
    if event_name == "session":
        print(f"[session] {payload['session_id']}")
        return
    if event_name == "text_delta":
        print_typewriter(payload.get("content", ""), char_delay)
        return
    if event_name == "thinking_delta":
        print(f"\n[thinking] {payload.get('content', '')}", end="", flush=True)
        return
    if event_name == "tool_status":
        print(f"\n[tool] {payload.get('name')} {payload.get('status')}")
        return
    if event_name == "error":
        print(f"\n[error] {payload.get('message')}", file=sys.stderr)
        return
    if event_name == "done":
        print(f"\n[done] {payload['session_id']}")


def print_typewriter(text: str, char_delay: float) -> None:
    for char in text:
        print(char, end="", flush=True)
        if char_delay > 0:
            time.sleep(char_delay)


if __name__ == "__main__":
    main()
