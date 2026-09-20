import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const gameplayRoot = process.env.AXM_GAMEPLAY_ABILITY_ROOT;
const outDir = process.env.AXM_AUDIO_PROOF_OUT;

if (!gameplayRoot) throw new Error("AXM_GAMEPLAY_ABILITY_ROOT is required");
if (!outDir) throw new Error("AXM_AUDIO_PROOF_OUT is required");

const api = await import(pathToFileURL(path.resolve(gameplayRoot, "src/api.mjs")).href);
const request = JSON.parse(fs.readFileSync(path.join(outDir, "gameplay-request.json"), "utf8"));
const externalReceipt = JSON.parse(fs.readFileSync(path.join(outDir, "audio-external-receipt.json"), "utf8"));

const bound = api.bindExternalRuntimeCueReceipt(request, externalReceipt);
fs.writeFileSync(path.join(outDir, "gameplay-bound-receipt.json"), JSON.stringify(bound, null, 2) + "\n");
