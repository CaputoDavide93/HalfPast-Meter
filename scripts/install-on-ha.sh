#!/usr/bin/env bash
# Install the committed bright_meter onto a Home Assistant host over SSH.
#
# For development copies; users install through HACS. Ships HEAD
# only (git archive), never the working tree, so what runs is what is
# committed. The new copy is unpacked beside the old one and swapped in, so a
# failed transfer leaves the installed version untouched.
#
#   scripts/install-on-ha.sh            # install, then restart HA yourself
#   scripts/install-on-ha.sh --restart  # install and run `ha core restart`
#
# Settings come from .env (see .env.example): HA_SSH_TARGET, HA_CONFIG_DIR.
set -euo pipefail

cd "$(dirname "$0")/.."

restart=false
case "${1:-}" in
  "") ;;
  --restart) restart=true ;;
  *) echo "usage: $0 [--restart]" >&2; exit 2 ;;
esac

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  . ./.env
  set +a
fi
: "${HA_SSH_TARGET:?set HA_SSH_TARGET in .env (see .env.example)}"
HA_CONFIG_DIR="${HA_CONFIG_DIR:-/config}"
if [[ "$HA_CONFIG_DIR" != /?* ]]; then
  echo "HA_CONFIG_DIR must be an absolute path, got '$HA_CONFIG_DIR'" >&2
  exit 2
fi

if [[ -n "$(git status --porcelain -- custom_components/bright_meter)" ]]; then
  echo "custom_components/bright_meter has uncommitted changes; commit first." >&2
  exit 1
fi

version="$(python3 -c 'import json; print(json.load(open("custom_components/bright_meter/manifest.json"))["version"])')"
commit="$(git rev-parse --short HEAD)"
echo "Installing bright_meter $version ($commit) on $HA_SSH_TARGET:$HA_CONFIG_DIR"

git archive HEAD custom_components/bright_meter |
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$HA_SSH_TARGET" \
    "set -e
     mkdir -p '$HA_CONFIG_DIR/custom_components'
     cd '$HA_CONFIG_DIR/custom_components'
     rm -rf bright_meter.new bright_meter.old
     mkdir bright_meter.new
     tar -xf - -C bright_meter.new --strip-components=2
     test -f bright_meter.new/manifest.json
     if [ -d bright_meter ]; then mv bright_meter bright_meter.old; fi
     mv bright_meter.new bright_meter
     rm -rf bright_meter.old"

if $restart; then
  echo "Restarting Home Assistant"
  ssh -o BatchMode=yes "$HA_SSH_TARGET" "ha core restart"
else
  echo "Installed. Restart Home Assistant to load it."
fi
