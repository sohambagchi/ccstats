# System ssh and rsync before the bundled ones

- The package's wrapper appends nix's rsync, openssh and iproute2 to PATH instead of prepending them; the dev shell no longer brings its own ssh or rsync
- CloudLab pulls work again: nix's OpenSSH refused a group-writable `~/.ssh/config` that Debian's accepts, failing every pull with rsync code 255
