#!/usr/bin/env bash
set -euo pipefail

# Expected environment variables:
#   REMOTE_BASE  : base dir on server, e.g. /opt/nanobot/dev
#   BUILD_ID     : Jenkins build number
#   PACKAGE_PATH : uploaded tar.gz absolute path

REMOTE_BASE="${REMOTE_BASE:-/home/u9000/tavily_search_nanobot-dev}"
BUILD_ID="${BUILD_ID:-manual_$(date +%Y%m%d%H%M%S)}"
PACKAGE_PATH="${PACKAGE_PATH:-${REMOTE_BASE}/release-${BUILD_ID}.tar.gz}"
RUNTIME_DIR="${REMOTE_BASE}/runtime"
UV_INSTALL_DIR="${UV_INSTALL_DIR:-${RUNTIME_DIR}/uv}"
UV_BIN="${UV_BIN:-${UV_INSTALL_DIR}/bin/uv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
PIP_RETRIES="${PIP_RETRIES:-8}"

RELEASES_DIR="${REMOTE_BASE}/releases"
RELEASE_DIR="${RELEASES_DIR}/${BUILD_ID}"
CURRENT_LINK="${REMOTE_BASE}/current"
PREV_LINK="${REMOTE_BASE}/previous"
VENV_DIR="${VENV_DIR:-${REMOTE_BASE}/.venv}"
NB_PYTHON="${VENV_DIR}/bin/python"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:5005/}"
# App startup time can fluctuate on busy hosts (venv warmup, imports, IO).
# Keep a wider default health-check window to avoid false rollback.
HEALTH_RETRY="${HEALTH_RETRY:-30}"
HEALTH_SLEEP_SEC="${HEALTH_SLEEP_SEC:-2}"
FORCE_REINSTALL="${FORCE_REINSTALL:-0}"
DEPS_HASH_FILE="${REMOTE_BASE}/.deps.hash"

START_SCRIPT="${REMOTE_BASE}/scripts/deploy/start.sh"
STOP_SCRIPT="${REMOTE_BASE}/scripts/deploy/stop.sh"
RESTART_SCRIPT="${REMOTE_BASE}/scripts/deploy/restart.sh"

echo "[deploy] REMOTE_BASE=${REMOTE_BASE}"
echo "[deploy] BUILD_ID=${BUILD_ID}"
echo "[deploy] PACKAGE_PATH=${PACKAGE_PATH}"

if [[ ! -f "${PACKAGE_PATH}" ]]; then
  echo "[deploy] package not found: ${PACKAGE_PATH}" >&2
  exit 1
fi

mkdir -p "${RELEASES_DIR}"
mkdir -p "${REMOTE_BASE}/logs"
mkdir -p "${REMOTE_BASE}/run"
mkdir -p "${REMOTE_BASE}/scripts/deploy"
mkdir -p "${RUNTIME_DIR}"
mkdir -p "${RUNTIME_DIR}/workspaces"

if [[ -L "${CURRENT_LINK}" ]]; then
  CURRENT_TARGET="$(readlink -f "${CURRENT_LINK}")"
  if [[ -n "${CURRENT_TARGET}" && -d "${CURRENT_TARGET}" ]]; then
    ln -sfn "${CURRENT_TARGET}" "${PREV_LINK}"
    echo "[deploy] backup previous -> ${CURRENT_TARGET}"
  fi
fi

rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"
tar xzf "${PACKAGE_PATH}" -C "${RELEASE_DIR}"

# Always use shared runtime data outside release snapshots.
# This prevents session/workspace data under runtime/ from being replaced
# when a new release is unpacked and `current` switches to it.
rm -rf "${RELEASE_DIR}/runtime"
ln -sfn "${RUNTIME_DIR}" "${RELEASE_DIR}/runtime"

# Ensure deploy helper scripts exist under REMOTE_BASE.
# Prefer fresh scripts from current release to keep script behavior in sync.
if [[ -f "${RELEASE_DIR}/scripts/deploy/start.sh" ]]; then
  cp -f "${RELEASE_DIR}/scripts/deploy/start.sh" "${START_SCRIPT}"
fi
if [[ -f "${RELEASE_DIR}/scripts/deploy/stop.sh" ]]; then
  cp -f "${RELEASE_DIR}/scripts/deploy/stop.sh" "${STOP_SCRIPT}"
fi
if [[ -f "${RELEASE_DIR}/scripts/deploy/restart.sh" ]]; then
  cp -f "${RELEASE_DIR}/scripts/deploy/restart.sh" "${RESTART_SCRIPT}"
fi
chmod +x "${START_SCRIPT}" "${STOP_SCRIPT}" "${RESTART_SCRIPT}" || true

ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
echo "[deploy] current -> ${RELEASE_DIR}"

RESOLVED_UV_BIN=""
if [[ ! -x "${UV_BIN}" ]]; then
  echo "[deploy] installing uv into ${UV_INSTALL_DIR}"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="${UV_INSTALL_DIR}" sh || true
fi
if [[ -x "${UV_BIN}" ]]; then
  RESOLVED_UV_BIN="${UV_BIN}"
elif command -v uv >/dev/null 2>&1; then
  RESOLVED_UV_BIN="$(command -v uv)"
fi
export PATH="${UV_INSTALL_DIR}/bin:${PATH}"
if [[ -n "${RESOLVED_UV_BIN}" ]]; then
  echo "[deploy] using uv: ${RESOLVED_UV_BIN}"
else
  echo "[deploy] uv not available, fallback to python3 -m venv + pip"
fi

VENV_CREATED=0
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "[deploy] creating venv with Python ${PYTHON_VERSION}: ${VENV_DIR}"
  if [[ -n "${RESOLVED_UV_BIN}" ]]; then
    "${RESOLVED_UV_BIN}" venv --python "${PYTHON_VERSION}" "${VENV_DIR}"
  else
    python3 -m venv "${VENV_DIR}"
  fi
  VENV_CREATED=1
fi

ensure_pip_in_venv() {
  if "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  echo "[deploy] pip missing in venv, bootstrapping with ensurepip"
  if "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
    "${VENV_DIR}/bin/python" -m pip install -U pip >/dev/null 2>&1 || true
    return 0
  fi
  echo "[deploy] ensurepip unavailable, recreating venv with system python3"
  rm -rf "${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "[deploy] pip bootstrap failed in venv: ${VENV_DIR}" >&2
    exit 1
  fi
}

verify_interpreter_consistency() {
  if [[ ! -x "${NB_PYTHON}" ]]; then
    echo "[deploy] NB_PYTHON not executable: ${NB_PYTHON}" >&2
    exit 1
  fi

  echo "[deploy] interpreter check: NB_PYTHON=${NB_PYTHON}"
  "${NB_PYTHON}" -V

  local actual_python=""
  actual_python="$("${NB_PYTHON}" -c 'import sys; print(sys.executable)')"
  if [[ -z "${actual_python}" ]]; then
    echo "[deploy] failed to resolve sys.executable from ${NB_PYTHON}" >&2
    exit 1
  fi

  local resolved_nb=""
  local resolved_actual=""
  resolved_nb="$(readlink -f "${NB_PYTHON}" 2>/dev/null || printf '%s' "${NB_PYTHON}")"
  resolved_actual="$(readlink -f "${actual_python}" 2>/dev/null || printf '%s' "${actual_python}")"
  echo "[deploy] interpreter resolved: ${resolved_actual}"

  if [[ "${resolved_nb}" != "${resolved_actual}" ]]; then
    echo "[deploy] interpreter mismatch: NB_PYTHON=${resolved_nb}, sys.executable=${resolved_actual}" >&2
    exit 1
  fi

  NB_PYTHON="${actual_python}"
  export NB_PYTHON
  export WEB_POSIX_PYTHON="${NB_PYTHON}"
  export VIRTUAL_ENV="${VENV_DIR}"
}

pip_install_with_retry() {
  local attempt=1
  local max_attempts=3
  while (( attempt <= max_attempts )); do
    if "${VENV_DIR}/bin/python" -m pip --default-timeout "${PIP_DEFAULT_TIMEOUT}" --retries "${PIP_RETRIES}" install "$@"; then
      return 0
    fi
    echo "[deploy] pip install failed (attempt ${attempt}/${max_attempts}), retrying..."
    sleep $(( attempt * 2 ))
    attempt=$(( attempt + 1 ))
  done
  echo "[deploy] pip install failed after ${max_attempts} attempts" >&2
  return 1
}

compute_deps_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
    return 0
  fi
  "${NB_PYTHON}" -c "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"
}

CURRENT_DEPS_HASH="$(
  {
    if [[ -f "${RELEASE_DIR}/requirements.txt" ]]; then
      printf '### requirements.txt\n'
      cat "${RELEASE_DIR}/requirements.txt"
      printf '\n'
    fi
    if [[ -f "${RELEASE_DIR}/nanobot/pyproject.toml" ]]; then
      printf '### nanobot/pyproject.toml\n'
      cat "${RELEASE_DIR}/nanobot/pyproject.toml"
      printf '\n'
    fi
  } | compute_deps_hash
)"
PREVIOUS_DEPS_HASH="$(cat "${DEPS_HASH_FILE}" 2>/dev/null || true)"
SKIP_DEP_INSTALL=0
if [[ "${FORCE_REINSTALL}" == "1" ]]; then
  echo "[deploy] FORCE_REINSTALL=1, will install dependencies"
elif [[ "${VENV_CREATED}" == "1" ]]; then
  echo "[deploy] venv was newly created, will install dependencies"
elif [[ -n "${PREVIOUS_DEPS_HASH}" && "${CURRENT_DEPS_HASH}" == "${PREVIOUS_DEPS_HASH}" ]]; then
  SKIP_DEP_INSTALL=1
  echo "[deploy] dependency fingerprint unchanged, skipping dependency install"
else
  echo "[deploy] dependency fingerprint changed, installing dependencies"
fi

# This repo snapshot vendors nanobot source without stable packaging metadata
# in CI output. Install runtime dependencies explicitly for web runner path.
if [[ "${SKIP_DEP_INSTALL}" == "0" ]]; then
  DEPS_INSTALL_START_TS="$(date +%s)"
  if [[ -n "${RESOLVED_UV_BIN}" ]]; then
    "${RESOLVED_UV_BIN}" pip install --python "${VENV_DIR}/bin/python" \
      --default-timeout "${PIP_DEFAULT_TIMEOUT}" \
      --retries "${PIP_RETRIES}" \
      "Flask>=3.0.0,<4.0.0" \
      "loguru>=0.7.2" \
      "pydantic>=2.8.0" \
      "pydantic-settings>=2.4.0" \
      "PyYAML>=6.0.1" \
      "requests>=2.32.0" \
      "rich>=13.7.0" \
      "httpx>=0.27.0" \
      "socksio>=1.0.0" \
      "openai>=1.40.0" \
      "json-repair>=0.30.0" \
      "tiktoken>=0.7.0"
  else
    ensure_pip_in_venv
    pip_install_with_retry -U pip
    pip_install_with_retry \
      "Flask>=3.0.0,<4.0.0" \
      "loguru>=0.7.2" \
      "pydantic>=2.8.0" \
      "pydantic-settings>=2.4.0" \
      "PyYAML>=6.0.1" \
      "requests>=2.32.0" \
      "rich>=13.7.0" \
      "httpx>=0.27.0" \
      "socksio>=1.0.0" \
      "openai>=1.40.0" \
      "json-repair>=0.30.0" \
      "tiktoken>=0.7.0"
  fi
  printf '%s\n' "${CURRENT_DEPS_HASH}" > "${DEPS_HASH_FILE}"
  DEPS_INSTALL_END_TS="$(date +%s)"
  echo "[timing] dependency install took $((DEPS_INSTALL_END_TS - DEPS_INSTALL_START_TS))s"
fi

verify_interpreter_consistency

# Exec / skills often run `python3 -m ...`; uv-created venvs may only provide `python`.
# Prepend venv bin to PATH (see nanobot ExecTool) only helps if `python3` exists there.
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  ( cd "${VENV_DIR}/bin" && ln -sfn python python3 )
  echo "[deploy] ensured ${VENV_DIR}/bin/python3 -> python"
fi

# Fail fast before restart if pydantic stack or nanobot cannot be imported.
if [[ ! -d "${CURRENT_LINK}/nanobot" ]]; then
  echo "[deploy] nanobot source directory missing: ${CURRENT_LINK}/nanobot" >&2
  exit 1
fi
PYTHONPATH="${CURRENT_LINK}/nanobot${PYTHONPATH:+:${PYTHONPATH}}" \
  "${NB_PYTHON}" -c "import pydantic, pydantic_settings; import nanobot"
echo "[deploy] runtime import check passed (${NB_PYTHON})"

bash "${RESTART_SCRIPT}"

echo "[deploy] health check: ${HEALTH_URL}"
for i in $(seq 1 "${HEALTH_RETRY}"); do
  if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
    echo "[deploy] health check passed"
    exit 0
  fi
  echo "[deploy] health check retry ${i}/${HEALTH_RETRY}"
  sleep "${HEALTH_SLEEP_SEC}"
done

echo "[deploy] health check failed, rollback..." >&2
if [[ -L "${PREV_LINK}" ]]; then
  PREV_TARGET="$(readlink -f "${PREV_LINK}")"
  if [[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]]; then
    ln -sfn "${PREV_TARGET}" "${CURRENT_LINK}"
    bash "${RESTART_SCRIPT}" || true
    echo "[deploy] rolled back to ${PREV_TARGET}" >&2
  fi
fi

exit 1
