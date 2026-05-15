#!/usr/bin/env bash
set -euo pipefail

REMOTE_BASE="${REMOTE_BASE:-/home/u9000/tavily_search_nanobot-dev}"
PID_FILE="${REMOTE_BASE}/run/web.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "[stop] pid file not found, nothing to stop"
  exit 0
fi

PID="$(cat "${PID_FILE}" || true)"
if [[ -z "${PID}" ]]; then
  echo "[stop] empty pid, cleanup pid file"
  rm -f "${PID_FILE}"
  exit 0
fi

if ! kill -0 "${PID}" >/dev/null 2>&1; then
  echo "[stop] pid=${PID} not running, cleanup pid file"
  rm -f "${PID_FILE}"
  exit 0
fi

kill "${PID}" || true
for _ in $(seq 1 10); do
  if ! kill -0 "${PID}" >/dev/null 2>&1; then
    rm -f "${PID_FILE}"
    echo "[stop] stopped pid=${PID}"
    exit 0
  fi
  sleep 1
done

echo "[stop] force kill pid=${PID}"
kill -9 "${PID}" || true
rm -f "${PID_FILE}"
