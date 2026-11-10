# Compressed transfers; push always uses its restricted key

- Push and hub ssh pulls run rsync with `-z`: transcripts are JSON text and send about 5x fewer bytes
- Push ssh sets `ControlPath=none`, so a shared connection from the user's ssh config can't bypass the key's `rrsync` restriction; `ServerAliveInterval=15` drops dead links
- `push.sh install` still prints the `ccstats host add` line when enabling the systemd timer fails
