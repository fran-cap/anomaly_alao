"""AALO - Anomaly A-Life Optimization lab framework.

Domain-neutral A/B harness: any .ltx knob or MO2 mod toggle can be varied,
measured and scored.  See framework/README.md.
"""

from . import config, ideas, ltx, metrics, mo2, runner, snapshot, xraylog

__version__ = "0.1.0"

__all__ = ["config", "ltx", "mo2", "snapshot", "xraylog", "metrics", "runner", "ideas"]
