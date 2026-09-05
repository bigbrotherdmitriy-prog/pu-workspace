import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { build, preview } from "vite";

import viteConfig from "./vite.config.mjs";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const playwrightCli = path.join(frontendRoot, "node_modules", "@playwright", "test", "cli.js");
const playwrightConfig = path.join(frontendRoot, "playwright.config.ts");
const serverConfig = {
  ...viteConfig,
  configFile: false,
  root: frontendRoot,
  preview: { host: "127.0.0.1", port: 4179, strictPort: true },
};

let child;
let server;
let timedOut = false;

const relaySignal = (signal) => {
  if (child && !child.killed) child.kill(signal);
};
process.once("SIGINT", () => relaySignal("SIGINT"));
process.once("SIGTERM", () => relaySignal("SIGTERM"));

try {
  await build(serverConfig);
  server = await preview(serverConfig);

  const exitCode = await new Promise((resolve, reject) => {
    child = spawn(
      process.execPath,
      [playwrightCli, "test", "--config", playwrightConfig, ...process.argv.slice(2)],
      { cwd: frontendRoot, env: process.env, stdio: "inherit" },
    );
    const deadline = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, 330_000);
    deadline.unref();
    child.once("error", (error) => {
      clearTimeout(deadline);
      reject(error);
    });
    child.once("exit", (code) => {
      clearTimeout(deadline);
      resolve(code ?? 1);
    });
  });

  if (timedOut) console.error("Playwright exceeded the 330 second runner deadline.");
  process.exitCode = timedOut ? 1 : exitCode;
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (server) await server.close();
}
