import process from "node:process";
import readline from "node:readline";

import { loadConfig } from "./config.js";
import { LoopRuntime } from "./runtime.js";

function getArg(name: string): string | undefined {
  const idx = process.argv.indexOf(name);
  if (idx === -1) return undefined;
  return process.argv[idx + 1];
}

async function main() {
  const configPath = getArg("--config") ?? "./loop.yaml";
  const cfg = loadConfig(configPath);

  if (cfg.llm.apiKeyEnv && !process.env[cfg.llm.apiKeyEnv]) {
    console.error(`Missing API key env var: ${cfg.llm.apiKeyEnv}`);
    process.exit(2);
  }

  const rt = new LoopRuntime(cfg);
  await rt.start();

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  console.log("mcp-test-loop runtime running. Type text to steer. Commands: /stop, /help");

  rl.on("line", async (line) => {
    const text = line.trim();
    if (!text) return;

    if (text === "/stop") {
      await rt.stop();
      console.log("Stopped.");
      process.exit(0);
    }

    if (text === "/help") {
      console.log("/stop - stop runtime\nAnything else is sent as steering to the agent.");
      return;
    }

    await rt.steer(text);
  });

  process.on("SIGINT", async () => {
    await rt.stop();
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
