#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${RUNTIME_DIR:-${ROOT_DIR}/runtime}"
PID_FILE="${PID_FILE:-${RUNTIME_DIR}/web.pid}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/log}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/app.log}"

mkdir -p "${LOG_DIR}"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "[stop] pid file not found: ${PID_FILE}"
  exit 0
fi

PID="$(cat "${PID_FILE}" || true)"
if [[ -z "${PID}" ]]; then
  rm -f "${PID_FILE}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop: empty pid file removed" >> "${LOG_FILE}"
  echo "[stop] empty pid file removed"
  exit 0
fi

if ! kill -0 "${PID}" >/dev/null 2>&1; then
  rm -f "${PID_FILE}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop: process not running, pid=${PID}" >> "${LOG_FILE}"
  echo "[stop] process not running, pid file removed: pid=${PID}"
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop: stopping pid=${PID}" >> "${LOG_FILE}"
kill "${PID}" || true
for _ in $(seq 1 10); do
  if ! kill -0 "${PID}" >/dev/null 2>&1; then
    rm -f "${PID_FILE}"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop: stopped pid=${PID}" >> "${LOG_FILE}"
    echo "[stop] stopped: pid=${PID}"
    exit 0
  fi
  sleep 1
done

kill -9 "${PID}" || true
rm -f "${PID_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop: force stopped pid=${PID}" >> "${LOG_FILE}"
echo "[stop] force stopped: pid=${PID}"
