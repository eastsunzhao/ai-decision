"""Trigger a web chat and observe bwrap process tags in WSL.

Usage:
  python scripts/observe_bwrap_session.py --user-id 12001 --message "hello"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from urllib import parse, request


def _http_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wsl_bwrap_pids() -> list[str]:
    cmd = [
        "wsl",
        "-e",
        "bash",
        "-lc",
        "ps -eo pid,cmd | awk '/[b]wrap --bind \\/ \\/ --dev \\/dev --proc \\/proc/{print $1}'",
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    stdout = _decode_bytes(proc.stdout)
    return [line.strip() for line in stdout.splitlines() if line.strip().isdigit()]


def _wsl_env_for_pid(pid: str) -> dict[str, str]:
    cmd = [
        "wsl",
        "-e",
        "bash",
        "-lc",
        f"tr '\\0' '\\n' < /proc/{pid}/environ | grep '^NB_' || true",
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    stdout = _decode_bytes(proc.stdout)
    env: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v
    return env


def _decode_bytes(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "utf-16le", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5005")
    parser.add_argument("--user-id", default="10001")
    parser.add_argument("--message", default="hello")
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    user_id = str(args.user_id).strip() or "10001"

    create_url = f"{base}/api/sessions?{parse.urlencode({'user_id': user_id})}"
    session = _http_json("POST", create_url, payload={})
    session_id = session["id"]

    chat_payload = {"user_id": user_id, "session_id": session_id, "message": args.message}
    chat = _http_json("POST", f"{base}/api/chat", payload=chat_payload)
    job_id = chat["job_id"]

    deadline = time.time() + max(1.0, args.wait_seconds)
    matched: list[tuple[str, dict[str, str]]] = []
    while time.time() < deadline and not matched:
        for pid in _wsl_bwrap_pids():
            env = _wsl_env_for_pid(pid)
            if env.get("NB_USER_ID") == user_id and env.get("NB_SESSION_ID") == session_id:
                matched.append((pid, env))
        if not matched:
            time.sleep(0.25)

    print(f"user_id={user_id}")
    print(f"session_id={session_id}")
    print(f"job_id={job_id}")
    if not matched:
        print("bwrap_match=NOT_FOUND (try increasing --wait-seconds or WEB_BWRAP_DEBUG_HOLD_SECONDS)")
        return 1

    for pid, env in matched:
        print(f"bwrap_pid={pid}")
        print(f"NB_USER_ID={env.get('NB_USER_ID', '')}")
        print(f"NB_SESSION_ID={env.get('NB_SESSION_ID', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
