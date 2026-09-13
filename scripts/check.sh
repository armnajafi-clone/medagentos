#!/usr/bin/env bash
# Run every quality gate that is available in this environment.
# Lint and type check are skipped when their tools are not installed, so the
# script stays usable on a bare interpreter; CI installs the [dev] extra and
# therefore runs all of them.
set -uo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="src:plugins${PYTHONPATH:+:$PYTHONPATH}"
status=0

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
skip() { printf '    skipped: %s\n' "$1"; }

step "Lint (ruff)"
if python -m ruff --version >/dev/null 2>&1; then
    python -m ruff check src plugins tests || status=1
else
    skip "ruff is not installed (pip install -e '.[dev]')"
fi

step "Type check (mypy)"
if python -m mypy --version >/dev/null 2>&1; then
    python -m mypy || status=1
else
    skip "mypy is not installed (pip install -e '.[dev]')"
fi

step "Tests"
if python -m pytest --version >/dev/null 2>&1; then
    python -m pytest || status=1
else
    skip "pytest is not installed, falling back to unittest"
    python -m unittest discover -s tests -t . -v || status=1
fi

if [ "$status" -eq 0 ]; then
    printf '\n\033[32mAll available checks passed.\033[0m\n'
else
    printf '\n\033[31mChecks failed.\033[0m\n'
fi
exit "$status"
