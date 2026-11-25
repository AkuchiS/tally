#!/usr/bin/env bash
# tally installer — zero-dependency, local-first AI usage tracker.
set -euo pipefail

echo "Installing tally…"
if command -v pipx >/dev/null 2>&1; then
  pipx install --force "git+https://github.com/AkuchiS/tally"
elif command -v pip3 >/dev/null 2>&1; then
  pip3 install --user --upgrade "git+https://github.com/AkuchiS/tally"
else
  echo "Need pipx or pip3. Install Python 3.9+ first." >&2; exit 1
fi

echo
echo "Done. Get started:"
echo "  tally sync            # read local AI tool logs"
echo "  tally track claude max20"
echo "  tally status          # or: tally serve  (web dashboard)"
