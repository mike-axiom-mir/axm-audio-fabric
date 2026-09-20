# AXM Audio Fabric

AXM Audio Fabric is the **underlying sound creation and runtime machine** for AXM.

It owns reusable audio primitives and processing. It is not the music-composition product and it is not the drag-and-drop mixer UI.

## Purpose

Give humans, AI, deterministic programs, games, video tools, and other AXM systems one inspectable audio substrate for:

- waveform/audio clip state
- sampling and playback
- synthesis and procedural SFX
- envelopes and modulation
- pitch and time transformation
- filters, EQ, compression, limiting, distortion and dynamics
- fades, loops and crossfades
- spatial / positional audio
- buses, routing and sends
- runtime game cues
- deterministic seeded sound recipes
- import/export boundaries and provenance
- later reusable learned/AI-assisted sound proposals without hiding canonical state

A gameplay action should eventually be able to request:

`impact sound at position X -> Audio Fabric -> deterministic/runtime realization`

without embedding audio-engine logic inside the game.

## Separation

### Audio Fabric owns
**sound machinery**

### Music Maker owns
**composition, performance and adaptive music/voice creation**

### Sound Mixer owns
**human-friendly assembly, arranging and mixing over the same canonical audio state**

The three repositories should interoperate through explicit contracts rather than duplicate implementations.

## Core direction

```text
source / synthesis / human recording / AI proposal
                    |
                    v
              AUDIO ATOM
                    |
                    v
              CLIP / CUE
                    |
          processing / spatialization
                    |
                    v
              BUS / OUTPUT
                    |
        game / mixer / video / export
```

Core execution should remain offline-capable where practical. AI may help generate or shape sounds, but AI output is a proposal/source, not hidden canonical truth.

## Current executable core

The first dependency-light offline proving slice is implemented in `axm_audio/core.py`, with deterministic layered SFX composition in `axm_audio/layered.py`.

It currently supports:

- canonical `axm.audio-atom/v1` validation;
- sine, square, triangle and seeded-noise sources;
- linear frequency sweeps plus optional seeded noise mixing;
- attack/decay/sustain/release envelopes;
- low-pass, drive and gain effects;
- canonical `axm.audio-cue/v1` runtime requests;
- simple positional distance attenuation and equal-power stereo panning;
- canonical `axm.audio-layered-cue/v1` recipes that combine multiple existing cues with explicit offsets and gains;
- deterministic layered mixing with an explicit same-sample-rate boundary rather than hidden resampling;
- clipping evidence for layered mixes via pre-clip peak and clipped-channel-sample count;
- deterministic 16-bit stereo WAV export;
- render receipts containing canonical recipe identity, PCM hash and signal statistics;
- replay tests proving the same recipe reproduces the same sample sequence and WAV bytes in the tested implementation.

Run the tests:

```bash
python -m unittest discover -s tests -v
```

Render the included procedural zap example:

```bash
python example_render.py
```

This writes `out/procedural-zap.wav` and `out/procedural-zap.receipt.json`.

GitHub Actions runs both the unit suite and the render smoke test.

## Truth boundary

A successful render, stable hash, valid WAV, or signal measurement proves execution and replay properties only. It does **not** prove that the sound is aesthetically good, realistic, balanced, or production-ready; those claims require listening/creative evaluation.

Layered v1 also deliberately refuses mixed sample rates. That is an explicit capability boundary, not a promise that resampling has occurred.

## Next meaningful gaps

Do not rebuild the core. Extend it with bounded real capability, with likely next candidates including sample import with provenance, explicit resampling/pitch/time transforms, buses/sends, richer spatialization, or an explicit Gameplay Ability / Sound Mixer adapter.

See `START_HERE_NEXT.md` and `PROJECT.json`.
