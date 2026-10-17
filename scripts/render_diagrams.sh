#!/usr/bin/env bash
# Render docs/diagrams/*.mmd (Mermaid) to PNG next to each source.
#   ./scripts/render_diagrams.sh            # all diagrams
#   ./scripts/render_diagrams.sh 04         # only files starting with 04
# Needs Node.js; uses Google Chrome if installed (no Chromium download).
set -euo pipefail

cd "$(dirname "$0")/../docs/diagrams"
MMDC=(npx -y @mermaid-js/mermaid-cli@11.4.2)

puppeteer_cfg="$(mktemp)"
trap 'rm -f "$puppeteer_cfg"' EXIT
chrome="${CHROME_PATH:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[[ -x "$chrome" ]] || chrome="$(command -v google-chrome || command -v chromium || true)"
if [[ -n "$chrome" ]]; then
  export PUPPETEER_SKIP_DOWNLOAD=1
  printf '{"executablePath": "%s", "args": ["--no-sandbox"]}' "$chrome" >"$puppeteer_cfg"
else
  echo '{"args": ["--no-sandbox"]}' >"$puppeteer_cfg"
fi

for src in "${1:-}"*.mmd; do
  out="${src%.mmd}.png"
  echo "rendering $src → $out"
  "${MMDC[@]}" -q -i "$src" -o "$out" -c mermaid.json -p "$puppeteer_cfg" -b white -s 2
done
