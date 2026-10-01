import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

export function buildInfo(lockfile, { nodeVersion, pnpmVersion }) {
  const version = /^\d+\.\d+\.\d+$/;
  if (!version.test(nodeVersion) || !version.test(pnpmVersion)) {
    throw new Error("Actual Node and pnpm versions must be exact semver values");
  }
  return {
    node_version: nodeVersion,
    pnpm_version: pnpmVersion,
    lockfile_sha256: createHash("sha256").update(lockfile).digest("hex"),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: { lockfile: { type: "string" }, output: { type: "string" } },
  });
  if (!values.lockfile || !values.output) {
    throw new Error("--lockfile and --output are required");
  }
  const info = buildInfo(readFileSync(values.lockfile), {
    nodeVersion: process.versions.node,
    pnpmVersion: execFileSync("pnpm", ["--version"], { encoding: "utf8" }).trim(),
  });
  writeFileSync(values.output, JSON.stringify(info, null, 2) + "\n", {
    flag: "wx",
    mode: 0o644,
  });
}
