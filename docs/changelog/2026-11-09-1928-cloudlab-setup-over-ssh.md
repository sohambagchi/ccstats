# CloudLab setup runs over ssh from the hub

- `ccstats script cloudlab-setup | ssh <node> sh -s -- <project>` is the documented way to set up a node, since nodes may not reach spore; `curl` from the hub stays for nodes that can
