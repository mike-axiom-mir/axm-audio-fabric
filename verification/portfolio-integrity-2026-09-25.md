# Reject invalid PCM before clipping and receipt creation

Date: 2026-09-25 UTC

Base commit: `19246a121791e546defeb7d83743cc7f755826d3`

Status: experimental repair, not merged or promoted.

## Observed failure

pcm16_bytes([(NaN, 0)]) produced (32767, 0), silently turning invalid signal into positive full-scale PCM. The regression suite failed before the repair.

## Repair

Both channels are validated before clipping. Finite clipping and rounding remain unchanged; invalid samples cannot receive a render receipt.

## Verification

Command: `PYTHONPATH=src:. python -m unittest discover -s tests -v`

29 tests passed. No listening or aesthetic-quality claim.

Regression tests exercise invalid input and valid-state continuity. The full repository command above passed on the repaired working tree. No production-readiness, deployment, or CANON claim is made.
