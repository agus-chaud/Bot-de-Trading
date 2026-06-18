#!/usr/bin/env node
/** Copy dashboard JSON into public/ for Next.js (F1-04 / ADR-065). */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(root, "..");
const dest = path.join(webRoot, "public", "dashboard_payload.json");

const sources = [
  path.join(webRoot, "..", "data", "dashboard_payload.json"),
  path.join(webRoot, "fixtures", "dashboard_payload.json"),
];

for (const src of sources) {
  if (fs.existsSync(src)) {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
    console.log(`copy-payload: ${path.relative(webRoot, src)} → public/dashboard_payload.json`);
    process.exit(0);
  }
}

console.error("copy-payload: no dashboard_payload.json found (expected data/ or fixtures/)");
process.exit(1);
