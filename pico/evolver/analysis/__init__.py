"""Analysis contracts for the Evolver.

The public Failure Miner exports are lazy so importing an existing analysis
module (for example ``stability_bucket`` from the scorer) cannot introduce a
scoring/evaluation import cycle.
"""

__all__ = [
    "FailureEvidence",
    "FailureFinding",
    "FailureMiner",
    "FailureMiningReport",
    "PRESERVED_TURN_TERMINAL_KINDS",
]


def __getattr__(name):
    if name in __all__:
        from . import failure_miner

        return getattr(failure_miner, name)
    raise AttributeError(name)
