#!/usr/bin/env bash
#
# One entry point for the whole pipeline. Run ./build.sh with no arguments.
#
# Everything here is run through the project venv. Ubuntu ships no `python`
# alias, and system python3 fails twice over anyway: the package is installed
# into the venv, and 22.04 carries Pillow 9.0.1 where the pipeline needs 10+.
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
PY="$VENV/bin/python"
PACK="samples/Full_Pack"
PORT="${PORT:-8000}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  %s\033[0m\n' "$*"; }
die()  { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

# --- setup -----------------------------------------------------------------

cmd_setup() {
  bold "setup"
  if [ ! -x "$PY" ]; then
    # python3-venv is not installed on this box and needs sudo, so prefer
    # virtualenv, which does not. Fall back to venv where it does exist.
    if python3 -c 'import virtualenv' 2>/dev/null; then
      python3 -m virtualenv -q "$VENV"
    elif python3 -m venv "$VENV" 2>/dev/null; then
      :
    else
      die "cannot create $VENV. Install one of:
    pip install --user virtualenv
    sudo apt install python3-venv"
    fi
    info "created $VENV"
  fi
  # -e so edits to src/ take effect without reinstalling.
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -e ".[dev]"
  info "$("$PY" -c 'import PIL,sys;print(f"python {sys.version.split()[0]}, Pillow {PIL.__version__}")')"
}

need_venv() { [ -x "$PY" ] || cmd_setup; }

# --- clean -----------------------------------------------------------------

# Empty a generated directory while keeping the .gitkeep that makes git track
# it. assets/source/ is NEVER touched: it holds hand-authored, committed specs
# and templates, and `rm -rf assets` once deleted them.
empty_generated() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  find "$dir" -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf {} +
}

cmd_clean() {
  bold "clean"
  for d in working concepts metadata previews review production; do
    empty_generated "assets/$d"
  done
  info "generated output removed; assets/source left intact"
}

# --- producers -------------------------------------------------------------

cmd_ingest() {
  need_venv
  bold "ingest — third-party pack into canonical assets"
  if [ ! -d "$PACK" ]; then
    warn "$PACK not present (it is gitignored third-party art); skipping"
    return 0
  fi
  # Every pack described in packs/, named rather than globbed — a crashed test
  # can leave a throwaway pack file behind and a glob would ingest it.
  "$PY" -m pixelasset.ingest \
    --pack packs/full_pack.yaml \
    --pack packs/character.yaml
}

cmd_assets() {
  need_venv
  bold "build — Path C stage graph"
  local found=0
  # Discovered from the specs on disk rather than hardcoded, so a new asset is
  # picked up by adding assets/source/<id>/spec.yaml and nothing else.
  for spec in assets/source/*/spec.yaml; do
    [ -e "$spec" ] || continue
    local id
    id="$(basename "$(dirname "$spec")")"
    found=1
    if "$VENV/bin/pixelasset" build "$id" >/tmp/pixelasset-build.log 2>&1; then
      info "$(printf '%-12s ok' "$id")"
    else
      tail -20 /tmp/pixelasset-build.log >&2
      die "$id failed to build"
    fi
  done
  [ "$found" = 1 ] || warn "no specs under assets/source/"
}

# --- checks ----------------------------------------------------------------

cmd_test() {
  need_venv
  bold "tests"
  "$PY" -m pytest -q
}

cmd_manifest() {
  need_venv
  [ -f assets/production/index.json ] || { warn "nothing built yet"; return 0; }
  bold "manifest"
  "$PY" - <<'EOF'
import json
from collections import defaultdict
d = json.load(open("assets/production/index.json"))
print(f"  {len(d['assets'])} assets   pipeline {d['pipeline_version']}")
# By family: the manifest now holds more than horses, and listing a farmhand
# among the coats is how you notice a consumer is grouping them together too.
families = defaultdict(set)
for a in d["assets"]:
    if a.get("mode"):
        families[a.get("family") or "?"].add(a["variant"])
for family, subjects in sorted(families.items()):
    print(f"  {family:9s} : {', '.join(sorted(subjects))}")
props = [a["id"] for a in d["assets"] if not a.get("mode")]
if props:
    print(f"  no mode   : {', '.join(props)}")
EOF
}

# Browser smoke test. Optional: it needs node, playwright-core and a Chromium,
# none of which are project dependencies.
cmd_smoke() {
  bold "game smoke test"
  command -v node >/dev/null || { warn "node not installed; skipping"; return 0; }
  node -e 'require("playwright-core")' 2>/dev/null || {
    warn "playwright-core not installed (npm i playwright-core); skipping"; return 0; }
  "$PY" -m http.server 8765 >/dev/null 2>&1 &
  local server=$!
  trap 'kill '"$server"' 2>/dev/null || true' RETURN
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://localhost:8765/game/" && break || sleep 0.25
  done
  BASE_URL=http://localhost:8765 node game/smoke-test.js /tmp || die "game smoke test failed"
}

cmd_serve() {
  need_venv
  [ -f assets/production/index.json ] || warn "no assets built — run ./build.sh first"
  bold "serving on http://localhost:$PORT"
  info "game   http://localhost:$PORT/game/"
  info "viewer http://localhost:$PORT/game/viewer.html"
  info "anim   http://localhost:$PORT/game/anim.html"
  "$PY" -m http.server "$PORT"
}

cmd_all() {
  cmd_setup
  cmd_ingest
  cmd_assets
  cmd_test
  cmd_manifest
  echo
  bold "done"
  info "./build.sh serve    then open http://localhost:$PORT/game/anim.html"
}

usage() {
  cat <<'EOF'
usage: ./build.sh [command]

  (none)     setup, ingest, build every asset, test, summarise
  setup      create .venv and install the project
  ingest     third-party pack -> assets/  (skipped if samples/ is absent)
  assets     run the Path C stage graph over assets/source/*/spec.yaml
  test       pytest
  smoke      headless browser test of the game (needs node + playwright-core)
  manifest   summarise assets/production/index.json
  serve      http server on $PORT (default 8000)
  clean      remove generated output, keeping assets/source and .gitkeep files
EOF
}

case "${1:-all}" in
  all) cmd_all ;;
  setup|ingest|assets|test|smoke|manifest|serve|clean) "cmd_${1}" ;;
  -h|--help|help) usage ;;
  *) usage; die "unknown command: $1" ;;
esac
