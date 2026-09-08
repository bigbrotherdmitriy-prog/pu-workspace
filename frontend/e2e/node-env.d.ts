/** Minimal environment contract for Playwright config; no dependency on @types/node. */
declare const process: { env: Record<string, string | undefined> };
