import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const gameplayRoot = process.env.AXM_GAMEPLAY_ABILITY_ROOT;
const outDir = process.env.AXM_AUDIO_PROOF_OUT;

if (!gameplayRoot) throw new Error("AXM_GAMEPLAY_ABILITY_ROOT is required");
if (!outDir) throw new Error("AXM_AUDIO_PROOF_OUT is required");

const api = await import(pathToFileURL(path.resolve(gameplayRoot, "src/api.mjs")).href);

const ability = {
  id: "arc-slash",
  duration: 0.7,
  phases: [
    { id: "startup", start: 0, end: 0.2 },
    { id: "active", start: 0.2, end: 0.32 },
    { id: "recovery", start: 0.32, end: 0.7 }
  ],
  tracks: [
    {
      id: "audio",
      type: "audio",
      events: [
        { id: "windup-whoosh", time: 0, cue: "slash-windup" },
        { id: "impact-crack", time: 0.22, cue: "blade-impact" },
        { id: "recovery-rattle", time: 0.4, cue: "armor-rattle" }
      ]
    }
  ]
};

const batch = api.createRuntimeCueRequests(ability, {
  actionInstanceId: "attack-17",
  afterTime: 0.1,
  throughTime: 0.22,
  types: "audio",
  context: { actorId: "player-1" }
});

const request = batch.requests.find(entry => entry.eventId === "impact-crack");
if (!request) throw new Error("expected impact-crack audio request");

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "ability.json"), JSON.stringify(ability, null, 2) + "\n");
fs.writeFileSync(path.join(outDir, "gameplay-batch.json"), JSON.stringify(batch, null, 2) + "\n");
fs.writeFileSync(path.join(outDir, "gameplay-request.json"), JSON.stringify(request, null, 2) + "\n");
