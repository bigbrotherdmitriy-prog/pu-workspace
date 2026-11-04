import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ASSETS = "/* PU_BUILD_ASSETS */ []";
const REVISION = '/* PU_BUILD_REVISION */ "v2"';

export function buildShellWorker(source, assets, revision) {
  if (source.split(ASSETS).length !== 2 || source.split(REVISION).length !== 2) throw new Error("PWA shell build markers missing or ambiguous");
  if (!/^[a-f0-9]{64}$/.test(revision)) throw new Error("Invalid PWA build revision");
  if (!assets.length || assets.some(path => !path.startsWith("/new/") || path.startsWith("/new/api/") || path.includes("..") || !/\.(js|css)$/.test(path))) {
    throw new Error("Invalid PWA static asset list");
  }
  return source.replace(ASSETS, JSON.stringify([...new Set(assets)].sort()))
    .replace(REVISION, JSON.stringify(`build-${revision}`));
}

export function pwaShellPlugin() {
  let config;
  return {
    name: "pu-pwa-shell-assets",
    apply: "build",
    configResolved(value) { config = value; },
    writeBundle(output, bundle) {
      if (!output.dir || config.base !== "/new/" || !config.publicDir) throw new Error("PWA shell needs a directory build under /new/");
      const source = readFileSync(join(config.publicDir, "service-worker.js"), "utf8");
      const digest = createHash("sha256").update(source);
      const assets = [];
      for (const [name, item] of Object.entries(bundle).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
        if (name.endsWith(".map")) continue;
        digest.update(name).update(item.type === "chunk" ? item.code : item.source);
        if (/\.(js|css)$/.test(name)) assets.push(config.base + name);
      }
      for (const name of ["manifest.webmanifest", "pu-icon.svg", "pu-icon-192.png", "pu-icon-512.png", "pu-icon-maskable-512.png"]) {
        digest.update(name).update(readFileSync(join(config.publicDir, name)));
      }
      writeFileSync(join(output.dir, "service-worker.js"), buildShellWorker(source, assets, digest.digest("hex")));
    },
  };
}
