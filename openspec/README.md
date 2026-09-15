# openspec

The change-proposal workflow, shared with the Java repository. A change gets a directory under
`changes/` holding `proposal.md` (why, and what changes), `design.md` (the reasoning and the
evidence) and `tasks.md` (ordered work, each item citing its finding). Completed changes move to
`changes/archive/`.

## Why this is nearly empty

The Java repository's `openspec/changes/` holds 54 files, almost all of them proposals for
features that now exist: sharding, event streaming, OTel tracing, driver properties. **Those are
the Java port's history, and they did not transfer as documents** - their outcomes transferred as
code, and `docs/JAVA-PARITY-LEDGER.md` is the record of that, file by file.

What transfers is the workflow itself, and `config.yaml`, which is filled in here rather than
left as the template's placeholders.

## Where this port's findings went

`openspec/changes/fix-defects-found-porting-to-python` lives in **the Java repository**, not this
one. Porting the framework file by file surfaced defects in the *Java* code, and a proposal to
change Java code belongs where that code is. The two repositories already share this workflow in
that direction.

A proposal here is for a change to *this* port.

## What is open

Two decisions are recorded in `docs/DECISIONS-TO-REVIEW.md` rather than as proposals, because
neither has a preferred answer yet:

- **graceful shutdown on a signal.** Java installs a JVM shutdown hook by default. `atexit` does
  not fire on SIGTERM, so matching it means a process-wide signal handler - a much bigger
  imposition in a Python application than a shutdown hook is in a JVM.
- **connection-pool leak detection.** Hikari's `leakDetectionThreshold` has no counterpart, and a
  naive port is worse than nothing: a handler raising on check-in *strands* the connection, so
  the detector would cause the leak it looks for.

Write a proposal when one of them acquires an answer worth arguing for.
