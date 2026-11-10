#!/bin/sh
# Keep this CloudLab node's Claude Code transcripts on /proj so they outlive the node.
#
#   ccstats script cloudlab-setup | ssh <node> sh -s -- [PROJECT]   (from the hub)
#   curl -fsS @HUB_URL@/cloudlab-setup.sh | sh -s -- [PROJECT]       (on a node that reaches it)
#
# Moves ~/.claude/projects (and ~/.claudia/projects if that profile exists) to
# /proj/<PROJECT>/ccstats/<node fqdn>/<profile>/projects and symlinks it back.
# PROJECT defaults to the only directory under /proj. Safe to re-run; run it
# before starting Claude Code on the node (e.g. from the profile's startup
# script). The hub pulls /proj/<PROJECT>/ccstats/ over ssh; nothing runs here.
set -eu

proj=${1:-}
if [ -z "$proj" ]; then
  set -- /proj/*/
  if [ $# -ne 1 ] || [ ! -d "$1" ]; then
    echo "cloudlab-setup: pass the project name; /proj has: $(ls /proj 2>/dev/null | tr '\n' ' ')" >&2
    exit 2
  fi
  proj=$(basename "$1")
fi
[ -d "/proj/$proj" ] || { echo "cloudlab-setup: /proj/$proj does not exist" >&2; exit 2; }

node=$(hostname -f 2>/dev/null || hostname)
root="/proj/$proj/ccstats/$node"
PROFILES=${CCSTATS_PROFILES:-"claude=$HOME/.claude claudia=$HOME/.claudia"}

mkdir -p "$root"
chmod 700 "$root"   # /proj is shared with the project; transcripts are not

for spec in $PROFILES; do
  profile=${spec%%=*}
  dir=${spec#*=}
  # The default profile is always set up; others only where they exist.
  [ "$profile" = claude ] || [ -d "$dir" ] || continue
  dest="$root/$profile/projects"
  mkdir -p "$dest" "$dir"
  if [ -L "$dir/projects" ]; then
    echo "cloudlab-setup: $dir/projects -> $(readlink "$dir/projects") (already set up)"
  else
    if [ -d "$dir/projects" ]; then
      cp -a "$dir/projects/." "$dest/"
      mv "$dir/projects" "$dir/projects.before-ccstats"
      echo "cloudlab-setup: copied existing transcripts; original kept at $dir/projects.before-ccstats"
    fi
    ln -s "$dest" "$dir/projects"
    echo "cloudlab-setup: $dir/projects -> $dest"
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$dir/settings.json" <<'EOF'
import json, os, sys
p = sys.argv[1]
s = json.load(open(p)) if os.path.exists(p) else {}
if s.get("cleanupPeriodDays", 0) < 3650:
    s["cleanupPeriodDays"] = 3650
    with open(p, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
EOF
  fi
done
echo "cloudlab-setup: done; the hub pulls /proj/$proj/ccstats/ (config: [[pull]] kind = \"cloudlab\")"
