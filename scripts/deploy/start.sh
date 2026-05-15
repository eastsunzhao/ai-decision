#!/usr/bin/env bash
set -euo pipefail

REMOTE_BASE="${REMOTE_BASE:-/home/u9000/tavily_search_nanobot-dev}"
CURRENT_LINK="${REMOTE_BASE}/current"
VENV_DIR="${VENV_DIR:-${REMOTE_BASE}/.venv}"
NB_PYTHON="${VENV_DIR}/bin/python"
PID_FILE="${REMOTE_BASE}/run/web.pid"
LOG_FILE="${REMOTE_BASE}/logs/web.log"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-5005}"

mkdir -p "${REMOTE_BASE}/run" "${REMOTE_BASE}/logs"

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "[start] process already running: pid=${OLD_PID}"
    exit 0
  fi
fi

cd "${CURRENT_LINK}"
if [[ ! -x "${NB_PYTHON}" ]]; then
  echo "[start] python not found in venv: ${NB_PYTHON}" >&2
  exit 1
fi

# Dev deploy runs on Linux; prevent stale WSL override from changing interpreter.
unset WEB_WSL_PYTHON

nohup env \
  JENKINS_NODE_COOKIE="dontKillMe" \
  BUILD_ID="dontKillMe" \
  WEB_HOST="${WEB_HOST}" WEB_PORT="${WEB_PORT}" \
  NB_PYTHON="${NB_PYTHON}" \
  WEB_POSIX_PYTHON="${NB_PYTHON}" \
  VIRTUAL_ENV="${VENV_DIR}" \
  PATH="${VENV_DIR}/bin:${PATH}" \
  PYTHONPATH="${CURRENT_LINK}/nanobot${PYTHONPATH:+:${PYTHONPATH}}" \
  "${NB_PYTHON}" web/app.py >> "${LOG_FILE}" 2>&1 &

NEW_PID="$!"
echo "${NEW_PID}" > "${PID_FILE}"
echo "[start] started pid=${NEW_PID}, log=${LOG_FILE}"
