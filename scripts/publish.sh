#!/usr/bin/env bash
#
# Local package publish using a PyPI API token from .env (token-based twine upload).
#
# Trusted Publishing (OIDC) via .github/workflows/publish.yml remains the
# RECOMMENDED path for tagged releases — no long-lived credentials. This script
# is a LOCAL fallback for a solo maintainer who prefers to publish from their
# machine with a token kept in .env (which is gitignored).
#
# Usage:
#   scripts/publish.sh            build + twine check + upload to PyPI (prod)
#   scripts/publish.sh --test     ... upload to TestPyPI instead
#   scripts/publish.sh --check    build + twine check only (no creds, no upload)
#
# .env keys (see .env.example):
#   PYPI_TOKEN       PyPI API token (starts with 'pypi-'); used as the password
#                    with username '__token__'.
#   TESTPYPI_TOKEN   Optional. TestPyPI has its own accounts/tokens; used for
#                    --test. Falls back to PYPI_TOKEN with a warning if unset
#                    (a prod token will NOT authenticate against TestPyPI).
#
# The token is never printed and is passed to twine only via the environment
# of the upload subprocess.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

TARGET="pypi"
UPLOAD=1
for arg in "$@"; do
  case "$arg" in
    --test)  TARGET="testpypi" ;;
    --check) UPLOAD=0 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $arg (see --help)" >&2; exit 2 ;;
  esac
done

# Read one KEY=value from .env WITHOUT executing the file (no `source`, so a
# stray value can't run code or clobber unrelated vars). Last assignment wins;
# surrounding single/double quotes are stripped; '=' inside the value is kept.
env_get() {
  [ -f "$ENV_FILE" ] || { printf ''; return 0; }
  local line
  line="$(grep -E "^$1=" "$ENV_FILE" | tail -1 || true)"
  [ -n "$line" ] || { printf ''; return 0; }
  line="${line#*=}"
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}

echo ">> Building sdist + wheel"
rm -rf "$REPO_ROOT/dist"
python -m build "$REPO_ROOT"

echo ">> twine check --strict"
python -m twine check --strict "$REPO_ROOT"/dist/*

if [ "$UPLOAD" -eq 0 ]; then
  echo ">> --check: artifact valid; skipping upload."
  exit 0
fi

if [ "$TARGET" = "testpypi" ]; then
  TOKEN="$(env_get TESTPYPI_TOKEN)"
  if [ -z "$TOKEN" ]; then
    echo "!! TESTPYPI_TOKEN not set in $ENV_FILE; falling back to PYPI_TOKEN." >&2
    echo "!! Note: a production PyPI token will NOT authenticate against TestPyPI." >&2
    TOKEN="$(env_get PYPI_TOKEN)"
  fi
  DEST="TestPyPI"
else
  TOKEN="$(env_get PYPI_TOKEN)"
  DEST="PyPI"
fi

if [ -z "$TOKEN" ]; then
  echo "ERROR: no token found in $ENV_FILE (need PYPI_TOKEN, or TESTPYPI_TOKEN for --test)." >&2
  exit 1
fi

echo ">> Uploading dist/* to $DEST (token from $ENV_FILE, user __token__)"
# Branch rather than expand a possibly-empty array — "${arr[@]}" on an empty
# array trips `set -u` ("unbound variable") on macOS's bash 3.2.
if [ "$TARGET" = "testpypi" ]; then
  TWINE_USERNAME="__token__" TWINE_PASSWORD="$TOKEN" \
    python -m twine upload --repository-url "https://test.pypi.org/legacy/" "$REPO_ROOT"/dist/*
else
  TWINE_USERNAME="__token__" TWINE_PASSWORD="$TOKEN" \
    python -m twine upload "$REPO_ROOT"/dist/*
fi
echo ">> Done."
