#!/bin/sh
# ccstats push: send this machine's Claude Code transcripts to the ccstats hub.
#
#   curl -fsS @HUB_URL@/push.sh | sh -s -- install [NAME]
#   ccstats-push run [-q]    push now (what the timer runs every 15 minutes)
#   ccstats-push status      show settings and the timer
#   ccstats-push uninstall   remove the timer and script; key and settings stay
#
# Transport is rsync over ssh with a dedicated key that the hub restricts to
# writing into its archive/<NAME>/ directory. Only *.jsonl transcripts and
# their small *.json sidecars are sent; nothing on this machine is deleted.
# Every command reports what it does; `run -q` prints only errors and a summary.
set -eu

conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/ccstats"
conf="$conf_dir/push.env"
key="$HOME/.ssh/ccstats_push"
bin="$HOME/.local/bin/ccstats-push"

HUB_SSH='@HUB_SSH@'
HUB_URL='@HUB_URL@'
NAME=$(hostname -s 2>/dev/null || hostname)
PROFILES="claude=$HOME/.claude claudia=$HOME/.claudia"
# shellcheck disable=SC1090
[ -f "$conf" ] && . "$conf"
HUB_SSH=${CCSTATS_HUB_SSH:-$HUB_SSH}
HUB_URL=${CCSTATS_HUB_URL:-$HUB_URL}
NAME=${CCSTATS_NAME:-$NAME}
PROFILES=${CCSTATS_PROFILES:-$PROFILES}

quiet=
say() { printf 'ccstats-push: %s\n' "$*"; }
info() { [ -n "$quiet" ] || say "$@"; }

# Keep transcripts for ~10 years instead of Claude Code's default 30 days.
set_retention() {
  settings="$1/settings.json"
  command -v python3 >/dev/null 2>&1 || { say "no python3: set \"cleanupPeriodDays\": 3650 in $settings by hand"; return; }
  python3 - "$settings" <<'EOF'
import json, os, sys
p = sys.argv[1]
s = {}
if os.path.exists(p):
    with open(p) as f:
        s = json.load(f)
old = s.get("cleanupPeriodDays")
if (old or 0) < 3650:
    s["cleanupPeriodDays"] = 3650
    with open(p, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(f"ccstats-push:   cleanupPeriodDays {'unset (30-day default)' if old is None else old} -> 3650 in {p}")
else:
    print(f"ccstats-push:   cleanupPeriodDays already {old} in {p}")
EOF
}

do_run() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"${TMPDIR:-/tmp}/ccstats-push.$(id -u).lock"
    flock -n 9 || { say "another push is running"; exit 0; }
  fi
  start=$(date +%s)
  info "pushing as $NAME to $HUB_SSH (key $key)"
  # -v lists each file sent and ends with a bytes summary; --progress only on a terminal.
  verbose="-v"
  [ -t 1 ] && verbose="-v --progress"
  [ -n "$quiet" ] && verbose=""
  sent=0
  for spec in $PROFILES; do
    profile=${spec%%=*}
    dir=${spec#*=}
    if [ ! -d "$dir/projects" ]; then
      info "$profile: skipped, no $dir/projects"
      continue
    fi
    info "$profile: $dir/projects ($(find "$dir/projects" -name '*.jsonl' | wc -l | tr -d ' ') transcripts) -> $HUB_SSH:$profile/projects/"
    # -R with /./ recreates projects/ under <profile>/ on the hub; -z because
    # transcripts are JSON text and compress about 5x.
    # shellcheck disable=SC2086
    rsync -aRz $verbose --timeout=120 --prune-empty-dirs \
      --exclude=tool-results/ --exclude=memory/ --exclude=file-history/ \
      --include='*/' --include='*.jsonl' --include='*.json' --exclude='*' \
      -e "ssh -i $key -o IdentitiesOnly=yes -o ControlPath=none -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new" \
      "$dir/./projects/" "$HUB_SSH:$profile/"
    sent=$((sent + 1))
  done
  [ "$sent" -gt 0 ] || { say "no profiles with a projects/ directory in: $PROFILES"; exit 0; }
  # Ask the hub to fold the new data in now rather than at its next periodic refresh.
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' -X POST "$HUB_URL/api/refresh" 2>&1 | tr '\n' ' ' | sed 's/ *$//') || true
  elif command -v wget >/dev/null 2>&1; then
    code=$(wget -q -T 10 -O /dev/null --post-data= "$HUB_URL/api/refresh" 2>&1 && echo 202) || code=failed
  else
    code="skipped (no curl or wget)"
  fi
  if [ "$code" = 202 ]; then
    info "asked $HUB_URL to refresh: queued"
  else
    say "asking $HUB_URL to refresh: $code (the hub still picks the files up within 5 minutes)"
  fi
  say "pushed $sent profile(s) in $(( $(date +%s) - start ))s"
}

install_timer() {
  if [ "$(uname -s)" = Darwin ]; then
    plist="$HOME/Library/LaunchAgents/com.ccstats.push.plist"
    mkdir -p "$(dirname "$plist")"
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ccstats.push</string>
  <key>ProgramArguments</key><array><string>$bin</string><string>run</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ccstats-push.log</string>
</dict></plist>
EOF
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    say "launchd agent com.ccstats.push runs every 15 minutes"
  elif systemctl --user show-environment >/dev/null 2>&1; then
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    cat >"$unit_dir/ccstats-push.service" <<EOF
[Unit]
Description=Push Claude Code transcripts to the ccstats hub
After=network-online.target

[Service]
Type=oneshot
ExecStart=$bin run
EOF
    cat >"$unit_dir/ccstats-push.timer" <<EOF
[Unit]
Description=Push Claude Code transcripts to the ccstats hub every 15 minutes

[Timer]
OnCalendar=*:0/15
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    if systemctl --user enable --now ccstats-push.timer >/dev/null; then
      say "systemd user timer ccstats-push.timer runs every 15 minutes"
    else
      say "could not enable ccstats-push.timer; run '$bin run' periodically yourself"
    fi
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]; then
      say "lingering is off, so the timer only runs while you are logged in (enable: loginctl enable-linger $(id -un))"
    fi
  elif command -v crontab >/dev/null 2>&1; then
    { crontab -l 2>/dev/null | grep -v '# ccstats-push$' || true; echo "*/15 * * * * $bin run >/dev/null 2>&1 # ccstats-push"; } | crontab -
    say "cron entry runs every 15 minutes"
  else
    say "no systemd, launchd or cron: run '$bin run' periodically yourself"
  fi
}

do_install() {
  [ -n "${1:-}" ] && NAME=$1
  for tool in rsync ssh ssh-keygen; do
    command -v "$tool" >/dev/null 2>&1 || { say "missing $tool; install it first"; exit 1; }
  done
  mkdir -p "$conf_dir" "$(dirname "$bin")" "$HOME/.ssh"
  cat >"$conf" <<EOF
HUB_SSH='$HUB_SSH'
HUB_URL='$HUB_URL'
NAME='$NAME'
PROFILES='$PROFILES'
EOF
  say "settings: $conf (name $NAME, hub $HUB_SSH, $HUB_URL)"
  # Piped through sh there is no file to copy, so fetch a fresh copy from the hub.
  if [ -f "$0" ] && grep -q 'ccstats push' "$0" 2>/dev/null; then
    cp "$0" "$bin.tmp"
  else
    curl -fsS "$HUB_URL/push.sh" -o "$bin.tmp" 2>/dev/null || wget -q -O "$bin.tmp" "$HUB_URL/push.sh"
  fi
  chmod +x "$bin.tmp" && mv "$bin.tmp" "$bin"
  say "script: $bin"
  if [ -f "$key" ]; then
    say "key: $key (existing)"
  else
    ssh-keygen -q -t ed25519 -N '' -C "ccstats-push@$NAME" -f "$key"
    say "key: $key (new ed25519)"
  fi
  for spec in $PROFILES; do
    profile=${spec%%=*}
    dir=${spec#*=}
    if [ -d "$dir" ]; then
      say "profile $profile: $dir"
      set_retention "$dir"
    else
      say "profile $profile: not present ($dir), skipped"
    fi
  done
  install_timer
  echo
  say "installed as $bin (settings in $conf)"
  say "now register this machine on the hub ($HUB_SSH):"
  echo
  echo "    ccstats host add $NAME '$(cat "$key.pub")'"
  echo
  say "then test with: $bin run"
}

do_uninstall() {
  if [ "$(uname -s)" = Darwin ]; then
    launchctl unload "$HOME/Library/LaunchAgents/com.ccstats.push.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.ccstats.push.plist"
    say "removed launchd agent com.ccstats.push"
  fi
  if systemctl --user show-environment >/dev/null 2>&1; then
    systemctl --user disable --now ccstats-push.timer 2>/dev/null || true
    rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ccstats-push.service" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ccstats-push.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    say "removed systemd units ccstats-push.service and ccstats-push.timer"
  fi
  if command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -q '# ccstats-push$'; then
    crontab -l | grep -v '# ccstats-push$' | crontab -
    say "removed cron entry"
  fi
  rm -f "$bin"
  say "removed; kept $key and $conf (on the hub: ccstats host rm $NAME)"
}

do_status() {
  echo "name:     $NAME"
  echo "hub:      $HUB_SSH  ($HUB_URL)"
  echo "profiles: $PROFILES"
  echo "key:      $key $( [ -f "$key" ] && echo present || echo MISSING)"
  systemctl --user list-timers ccstats-push.timer 2>/dev/null | head -2 || true
}

case "${1:-}" in
  run) [ "${2:-}" = -q ] && quiet=1; do_run ;;
  install) do_install "${2:-}" ;;
  uninstall) do_uninstall ;;
  status) do_status ;;
  *) echo "usage: ccstats-push install [NAME] | run [-q] | status | uninstall" >&2; exit 2 ;;
esac
