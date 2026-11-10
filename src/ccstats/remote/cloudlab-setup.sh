#!/bin/sh
# Keep this CloudLab node's Claude Code transcripts on /proj so they outlive the node.
#
#   ccstats script cloudlab-setup | ssh <node> sh -s -- [-q] [PROJECT]   (from the hub)
#   curl -fsS @HUB_URL@/cloudlab-setup.sh | sh -s -- [-q] [PROJECT]       (on a node that reaches it)
#
# Moves ~/.claude/projects (and ~/.claudia/projects if that profile exists) to
# /proj/<PROJECT>/ccstats/<node fqdn>/<profile>/projects and symlinks it back.
# PROJECT defaults to the only directory under /proj. Safe to re-run; run it
# before starting Claude Code on the node (e.g. from the profile's startup
# script). The hub pulls /proj/<PROJECT>/ccstats/ over ssh; nothing runs here.
# Reports each step; -q prints only errors.
set -eu

quiet=
[ "${1:-}" = -q ] && { quiet=1; shift; }
say() { [ -n "$quiet" ] || printf 'cloudlab-setup: %s\n' "$*"; }
die() { printf 'cloudlab-setup: %s\n' "$*" >&2; exit 2; }

proj=${1:-}
if [ -z "$proj" ]; then
  set -- /proj/*/
  if [ $# -ne 1 ] || [ ! -d "$1" ]; then
    die "pass the project name; /proj has: $(ls /proj 2>/dev/null | tr '\n' ' ')"
  fi
  proj=$(basename "$1")
  say "project: $proj (the only directory in /proj)"
else
  say "project: $proj"
fi
[ -d "/proj/$proj" ] || die "/proj/$proj does not exist"

node=$(hostname -f 2>/dev/null || hostname)
root="/proj/$proj/ccstats/$node"
PROFILES=${CCSTATS_PROFILES:-"claude=$HOME/.claude claudia=$HOME/.claudia"}
say "node: $node"
say "transcripts go to: $root (mode 700)"

mkdir -p "$root"
chmod 700 "$root"   # /proj is shared with the project; transcripts are not

for spec in $PROFILES; do
  profile=${spec%%=*}
  dir=${spec#*=}
  # The default profile is always set up; others only where they exist.
  if [ "$profile" != claude ] && [ ! -d "$dir" ]; then
    say "$profile: skipped, no $dir"
    continue
  fi
  dest="$root/$profile/projects"
  mkdir -p "$dest" "$dir"
  if [ -L "$dir/projects" ]; then
    say "$profile: $dir/projects -> $(readlink "$dir/projects") (already set up)"
  else
    if [ -d "$dir/projects" ]; then
      n=$(find "$dir/projects" -name '*.jsonl' | wc -l | tr -d ' ')
      size=$(du -sh "$dir/projects" 2>/dev/null | cut -f1)
      say "$profile: copying $n transcripts ($size) from $dir/projects to $dest"
      cp -a "$dir/projects/." "$dest/"
      mv "$dir/projects" "$dir/projects.before-ccstats"
      say "$profile: original kept at $dir/projects.before-ccstats (delete it once satisfied)"
    else
      say "$profile: no existing transcripts in $dir/projects"
    fi
    ln -s "$dest" "$dir/projects"
    say "$profile: linked $dir/projects -> $dest"
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$dir/settings.json" "$profile" "$quiet" <<'EOF'
import json, os, sys
p, profile, quiet = sys.argv[1], sys.argv[2], sys.argv[3]
s = json.load(open(p)) if os.path.exists(p) else {}
old = s.get("cleanupPeriodDays")
if (old or 0) < 3650:
    s["cleanupPeriodDays"] = 3650
    with open(p, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    msg = f"cleanupPeriodDays {'unset (30-day default)' if old is None else old} -> 3650 in {p}"
else:
    msg = f"cleanupPeriodDays already {old} in {p}"
if not quiet:
    print(f"cloudlab-setup: {profile}: {msg}")
EOF
  else
    say "$profile: no python3; set \"cleanupPeriodDays\": 3650 in $dir/settings.json by hand"
  fi
done

say "done. The hub's ~/.config/ccstats/config.toml needs:"
[ -n "$quiet" ] || cat <<EOF

    [[pull]]
    name = "cloudlab"
    kind = "cloudlab"
    ssh = ["$(id -un)@$node"]
    path = "/proj/$proj/ccstats/"

EOF
