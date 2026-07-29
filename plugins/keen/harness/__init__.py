"""Keen UI capture, decomposition, and analysis engine.

The harness is intentionally split into stages that can run independently:

    capture    URL → screenshots + DOM dumps + computed styles
    decompose  DOM dump → flat list of component records
    analyze    component list → predicate failures + measurements
    tokens     screenshot/DOM → detected design tokens
    rubric     analysis → scored, severity-ranked findings
    report     everything → report.json + summary.md

Each stage writes its output to disk so later stages don't need to re-run earlier
ones. This is what makes it a harness rather than a script: the artifacts are
durable, inspectable, and replayable.

Conventions:
- All paths are pathlib.Path
- All disk output is JSON (machine) + Markdown (human) + PNG (pixels)
- Stages take and return dataclasses, never dicts
- Nothing in this package calls an LLM; all judgment is deferred to the caller
"""

__version__ = "0.8.1"
