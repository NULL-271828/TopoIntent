# Preparation host privilege boundary

The preparation user has no sudo/root privileges. No sudo, apt, Mininet process, iptables operation, Linux namespace change, or network configuration was executed. Generated Python was parsed, not imported/executed as Mininet code.

Generation and metric reconstruction require no root. Actual emulation is an explicit optional task on another privileged Linux environment. This mirrors the GPU-generation / network-validation transfer workflow in the supplied evaluation runners. It conflicts with the manuscript's same-host wording, which is left for author reconciliation.
