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

## Immediate proving target

Build the smallest real dependency-light audio core that can:

1. create or ingest a short sound;
2. apply an inspectable envelope/process chain;
3. produce a deterministic cue description;
4. trigger it from an external game-style request;
5. export or render evidence;
6. replay the same canonical recipe.

Do not claim good sound, realism, or production quality merely because bytes were produced.

See `START_HERE_NEXT.md` and `PROJECT.json`.
