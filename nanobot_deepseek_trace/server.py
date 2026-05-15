from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
TRACELOG_DIR = APP_DIR / "tracelog"


def _candidate_dirs(extra_dir: str | None) -> list[Path]:
    dirs = [
        TRACELOG_DIR,
    ]
    env_dir = os.environ.get("NANOBOT_DEEPSEEK_TRACE_DIR")
    if env_dir:
        dirs.insert(0, Path(env_dir).expanduser())
    if extra_dir:
        dirs.insert(0, Path(extra_dir).expanduser())
    seen: set[Path] = set()
    out: list[Path] = []
    for item in dirs:
        resolved = item.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def _trace_files(trace_dir: str | None) -> list[Path]:
    files: list[Path] = []
    env_path = os.environ.get("NANOBOT_DEEPSEEK_TRACE_PATH")
    if env_path:
        path = Path(env_path).expanduser().resolve(strict=False)
        if path.is_file():
            files.append(path)
    for directory in _candidate_dirs(trace_dir):
        if directory.is_dir():
            files.extend(sorted(directory.glob("*deepseek*trace*.jsonl")))
            files.extend(sorted(directory.glob("*.jsonl")))
    unique: dict[str, Path] = {}
    for path in files:
        if path.is_file():
            unique[str(path.resolve(strict=False))] = path.resolve(strict=False)
    return sorted(unique.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def _file_summary(path: Path) -> dict:
    total = 0
    bad = 0
    last_record = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            try:
                last_record = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "records": total,
        "bad_records": bad,
        "last_timestamp": last_record.get("timestamp") if isinstance(last_record, dict) else None,
        "last_model": last_record.get("model") if isinstance(last_record, dict) else None,
    }


def _load_records(path: Path, limit: int) -> dict:
    records: list[dict] = []
    bad_records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                bad_records.append({"line": line_no, "error": str(exc), "text": text[:500]})
                continue
            if isinstance(item, dict):
                item["_line"] = line_no
                records.append(item)
            else:
                bad_records.append({"line": line_no, "error": "record is not an object", "text": text[:500]})
    if limit > 0:
        records = records[-limit:]
    return {
        "file": _file_summary(path),
        "records": records,
        "bad_records": bad_records,
    }


def _safe_file_from_query(path_value: str, trace_dir: str | None) -> Path | None:
    requested = Path(unquote(path_value)).expanduser().resolve(strict=False)
    allowed = {str(path) for path in _trace_files(trace_dir)}
    if str(requested) not in allowed:
        return None
    return requested if requested.is_file() else None


class TraceHandler(BaseHTTPRequestHandler):
    trace_dir: str | None = None

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/files":
            files = [_file_summary(path) for path in _trace_files(self.trace_dir)]
            self._send_json({"files": files})
            return
        if parsed.path == "/api/traces":
            query = parse_qs(parsed.query)
            file_value = (query.get("file") or [""])[0]
            limit_value = (query.get("limit") or ["500"])[0]
            try:
                limit = max(0, min(int(limit_value), 5000))
            except ValueError:
                limit = 500
            path = _safe_file_from_query(file_value, self.trace_dir)
            if path is None:
                self._send_json({"error": "trace file is not available"}, status=404)
                return
            self._send_json(_load_records(path, limit))
            return

        if parsed.path in {"", "/"}:
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        static_path = (STATIC_DIR / parsed.path.lstrip("/")).resolve(strict=False)
        if STATIC_DIR.resolve(strict=False) in static_path.parents and static_path.is_file():
            if static_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif static_path.suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            else:
                content_type = "application/octet-stream"
            self._send_file(static_path, content_type)
            return
        self._send_json({"error": "not found"}, status=404)


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek trace JSONL viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8061)
    parser.add_argument("--trace-dir", default=None, help="directory containing DeepSeek trace JSONL files")
    args = parser.parse_args()

    TraceHandler.trace_dir = args.trace_dir
    server = ThreadingHTTPServer((args.host, args.port), TraceHandler)
    print(f"DeepSeek trace UI: http://{args.host}:{args.port}/")
    print("Scanning:")
    for directory in _candidate_dirs(args.trace_dir):
        print(f"  - {directory}")
    server.serve_forever()


if __name__ == "__main__":
    main()
