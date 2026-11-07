# CloudLab nodes keep transcripts on /proj; the hub pulls the tree

**Status:** accepted

**Context:** CloudLab nodes are short-lived; transcripts on a node's disk vanish with the experiment.
**Decision:** `cloudlab-setup.sh [PROJECT]` (run once per node) moves `~/.claude/projects` (and `~/.claudia/projects` if present) to `/proj/<PROJECT>/ccstats/<node fqdn>/<profile>/projects` (mode 700) and symlinks it back. The hub's collect timer rsyncs `/proj/<PROJECT>/ccstats/` from the first reachable ssh target in a `[[pull]] kind = "cloudlab"` entry.
**Consequences:** nodes run nothing and need no inventory; each node appears as its own host (its fqdn). The pull needs one live node or ops host per project.
