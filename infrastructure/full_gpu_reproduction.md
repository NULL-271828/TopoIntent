# Optional full GPU reproduction

See [FULL_REPRODUCTION](../docs/FULL_REPRODUCTION.md) for known 72B AWQ configuration, commands, unresolved serving metadata, original Qdrant path, sample-count scale-down, and transfer to Mininet.

The default AE never starts these services. A CPU evaluator independently runs retrieval and checks original downstream outputs. No author GPU account or remote service is needed. Multi-GPU public allocations may be difficult to obtain; this is the reason for releasing original LLM intermediates rather than making private hardware a default dependency.
