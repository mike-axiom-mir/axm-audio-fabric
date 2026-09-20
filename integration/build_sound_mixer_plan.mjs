import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const mixerRootRaw = process.env.AXM_SOUND_MIXER_ROOT;
if (!mixerRootRaw) throw new Error('AXM_SOUND_MIXER_ROOT is required');
if (process.argv.length !== 4) {
  throw new Error('usage: node integration/build_sound_mixer_plan.mjs <source-receipt.json> <mix-plan.json>');
}

const mixerRoot = resolve(mixerRootRaw);
const core = await import(pathToFileURL(resolve(mixerRoot, 'src/mixer-core.mjs')).href);
const placement = await import(pathToFileURL(resolve(mixerRoot, 'src/audio-fabric-placement.mjs')).href);
const sourceReceipt = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const artifact = sourceReceipt.rendered_artifact;

let project = core.createProject({
  id: 'audio-fabric-export-proof',
  title: 'Audio Fabric export proof',
  sampleRate: artifact.sample_rate
});
project = core.addTrack(project, { id: 'music', name: 'Music', gain: 0.8, pan: 0.25 });
project = placement.placeAudioFabricMusicRender(project, 'music', sourceReceipt, {
  id: 'music-clip',
  receiptRef: 'proof:source.receipt.json',
  startMs: 25,
  sourceOffsetMs: 10,
  durationMs: 180,
  loop: false,
  fadeInMs: 15,
  fadeOutMs: 25,
  gain: 0.5
});

const plan = core.buildMixPlan(project);
writeFileSync(process.argv[3], `${JSON.stringify(plan, null, 2)}\n`, 'utf8');
