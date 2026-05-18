#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/envs/conda_python311_14/bin/python}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-5005}"
RUNTIME_DIR="${RUNTIME_DIR:-${ROOT_DIR}/runtime}"
PID_FILE="${PID_FILE:-${RUNTIME_DIR}/web.pid}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/log}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/app.log}"
CONSOLE_LOG_FILE="${CONSOLE_LOG_FILE:-${LOG_DIR}/console.log}"
APP_LOG_LEVEL="${APP_LOG_LEVEL:-DEBUG}"

mkdir -p "${RUNTIME_DIR}" "${LOG_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[start] python not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

log_line() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${LOG_FILE}"
}

if ! PRECHECK_OUTPUT="$(
  PYTHONPATH="${ROOT_DIR}/nanobot${PYTHONPATH:+:${PYTHONPATH}}" \
  "${PYTHON_BIN}" - <<'PY' 2>&1
from typing_extensions import Sentinel
import flask
import openai
import pydantic
import pydantic_settings
import yaml

print("startup dependency precheck ok")
PY
)"; then
  log_line "startup dependency precheck failed"
  {
    echo "${PRECHECK_OUTPUT}"
    echo
    echo "Suggested fix:"
    echo "  ${PYTHON_BIN} -m pip install --force-reinstall 'pydantic==2.12.5' 'pydantic-core==2.41.5' 'pydantic-settings>=2.12,<3'"
    echo
  } >> "${LOG_FILE}"
  echo "[start] dependency precheck failed; see ${LOG_FILE}" >&2
  exit 1
fi
log_line "${PRECHECK_OUTPUT}"

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" >/dev/null 2>&1; then
    echo "[start] already running: pid=${OLD_PID}"
    echo "[start] url=http://${WEB_HOST}:${WEB_PORT}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

cd "${ROOT_DIR}"

nohup env \
  PYTHONPATH="${ROOT_DIR}/nanobot${PYTHONPATH:+:${PYTHONPATH}}" \
  NB_PYTHON="${PYTHON_BIN}" \
  WEB_POSIX_PYTHON="${PYTHON_BIN}" \
  CONDA_DEFAULT_ENV="${CONDA_DEFAULT_ENV:-conda_python311_14}" \
  CONDA_PREFIX="${CONDA_PREFIX:-/opt/anaconda3/envs/conda_python311_14}" \
  APP_LOG_FILE="${LOG_FILE}" \
  APP_LOG_LEVEL="${APP_LOG_LEVEL}" \
  PATH="$(dirname "${PYTHON_BIN}"):${PATH}" \
  WEB_HOST="${WEB_HOST}" \
  WEB_PORT="${WEB_PORT}" \
  "${PYTHON_BIN}" web/app.py >> "${CONSOLE_LOG_FILE}" 2>&1 &

PID="$!"
echo "${PID}" > "${PID_FILE}"

echo "[start] started: pid=${PID}"
echo "[start] url=http://${WEB_HOST}:${WEB_PORT}"
echo "[start] log=${LOG_FILE}"
echo "[start] log_level=${APP_LOG_LEVEL}"
echo "[start] console_log=${CONSOLE_LOG_FILE}"
