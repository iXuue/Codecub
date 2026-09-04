# Codecub Phase 0 baselines

`scripts/run_baseline.py` writes the current Runtime, retrieval, reliability,
regression, context and multi-agent baseline records here. Every record embeds
the commit, dirty-worktree state, benchmark version, environment and timestamp.

Context and real-model multi-agent measurements are deliberately marked
`not_run` when no explicit provider configuration is available; absence of a
provider must not be presented as a successful measurement.

## Provenance status

The currently retained `codecub-phase0-v1` snapshot has
`baseline_validity: NOT_AVAILABLE`. Its Git commit is also the current dirty
worktree's commit, and there is no immutable pre-Runtime tree hash or external
attestation. It remains a reference snapshot only; it must not be described as
a proven pre-upgrade baseline. Future baselines must be captured from a clean,
identified revision before implementation begins, with model/provider and
environment explicitly recorded.
