// Rebuild public/data/ from the project's Domino dataset mounts using the committed manifest.
//
// public/data/ is gitignored (attached/uploaded data never ships in git — required so sensitive
// data can't leak into the app's GitHub repo). The builder records every attachment in the committed
// manifest .sage/attachments.json; this script recreates the public/data/ symlinks from the local
// dataset mounts (present on the App hardware — same Domino project as the builder) so that /data/...
// resolves in the PUBLISHED app. Runs before `vite build` (see app.sh), which then bakes the linked
// files into dist/. Node built-ins only, no deps. No-op when the manifest is absent or empty.
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, symlinkSync } from "node:fs";
import { dirname, join } from "node:path";

const MANIFEST = ".sage/attachments.json";
// Mirror backend/sage/assets/provider.py resolve_mount_roots(): env overrides first, then defaults.
const DEFAULT_ROOTS = ["/domino/datasets/local", "/mnt/data", "/mnt/imported/data"];

function mountRoots() {
  const roots = [];
  for (const key of ["DOMINO_DATASET_MOUNT_PATH", "DOMINO_MOUNT_PATHS"]) {
    const raw = process.env[key];
    if (raw) for (const p of raw.split(/[:,]/)) { const t = p.trim(); if (t) roots.push(t); }
  }
  roots.push(...DEFAULT_ROOTS);
  return [...new Set(roots)];
}

function mountFor(datasetName) {
  for (const root of mountRoots()) {
    const p = join(root, datasetName);
    try { if (statSync(p).isDirectory()) return p; } catch { /* not here */ }
  }
  return null;
}

function main() {
  if (!existsSync(MANIFEST)) return;
  let entries;
  try { entries = JSON.parse(readFileSync(MANIFEST, "utf8")); } catch { return; }
  if (!Array.isArray(entries)) return;

  let linked = 0;
  let missing = 0;
  for (const e of entries) {
    const rel = e && e.path;                         // workspace-rel: public/data/<slug>/...
    const dataset = e && e.dataset;                  // real dataset name (mounts at <root>/<name>)
    const dsRel = e && (e.dataset_rel_path || e.file); // path within the dataset
    if (!rel || !dataset || !dsRel || !rel.startsWith("public/data/")) continue;
    const mount = mountFor(dataset);
    const src = mount && join(mount, dsRel);
    if (!src || !existsSync(src)) { missing++; continue; }
    mkdirSync(dirname(rel), { recursive: true });
    try { rmSync(rel, { force: true }); } catch { /* nothing to remove */ }
    try { symlinkSync(src, rel); linked++; } catch (err) { console.error(`[rehydrate] ${rel}: ${err.message}`); }
  }
  console.log(`[rehydrate] linked ${linked} data file(s)${missing ? `, ${missing} unavailable` : ""}`);
}

main();
