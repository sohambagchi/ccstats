#!/usr/bin/env bash
# Install or upgrade the ccstats hub on this machine as systemd user units:
#
#   ccstats-serve.service   HTTP API + dashboard, refreshes the database every 5 min and on push
#   ccstats-collect.timer   every 15 min: snapshot this machine's transcripts, run ssh pulls, compute
#
# Builds the package from this checkout into a pinned GC root, so later builds and dev work don't
# change what the units run. Re-run to upgrade. Writes ~/.config/ccstats/config.toml if missing.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state=${XDG_STATE_HOME:-$HOME/.local/state}/ccstats
root=$state/current
conf=${XDG_CONFIG_HOME:-$HOME/.config}/ccstats/config.toml
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
nix=$(command -v nix || echo /nix/var/nix/profiles/default/bin/nix)

mkdir -p "$state" "$unit_dir" "$(dirname "$conf")"
echo "building $repo -> $root"
"$nix" build "$repo#ccstats" -o "$root"
store=$(readlink -f "$root")
# Through the GC root, so upgrades retarget the command too.
mkdir -p "$HOME/.local/bin"
ln -sfn "$root/bin/ccstats" "$HOME/.local/bin/ccstats"

if [[ ! -f $conf ]]; then
  cat >"$conf" <<EOF
# ccstats hub settings; every key is optional (see src/ccstats/config.py).
listen = "0.0.0.0:8420"
hub_ssh = "$USER@$(hostname -s)"

# CloudLab: nodes keep transcripts on /proj (cloudlab-setup.sh); the hub pulls them.
# [[pull]]
# name = "utah"
# kind = "cloudlab"
# ssh = ["<you>@<any live node>"]
# path = "/proj/<project>/ccstats/"
EOF
  echo "wrote $conf"
fi

cat >"$unit_dir/ccstats-serve.service" <<EOF
[Unit]
Description=ccstats hub: HTTP API and dashboard
After=network-online.target

[Service]
# $store
ExecStart=$root/bin/ccstats serve
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

cat >"$unit_dir/ccstats-collect.service" <<EOF
[Unit]
Description=ccstats hub: archive local transcripts, run ssh pulls, compute

[Service]
Type=oneshot
ExecStart=$root/bin/ccstats collect
EOF

cat >"$unit_dir/ccstats-collect.timer" <<EOF
[Unit]
Description=ccstats collect every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user start ccstats-collect.service
systemctl --user enable --now ccstats-collect.timer
systemctl --user enable ccstats-serve.service
systemctl --user restart ccstats-serve.service

if [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) != yes ]]; then
  echo "note: lingering is off, so the units stop when you log out (loginctl enable-linger $USER)"
fi
systemctl --user --no-pager status ccstats-serve.service | head -4
