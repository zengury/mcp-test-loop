import fs from "node:fs";
import yaml from "js-yaml";

export type LoopConfig = {
  llm: {
    provider: string;
    model: string;
    apiKeyEnv?: string;
    thinkingLevel?: "off" | "minimal" | "low" | "medium" | "high" | "xhigh";
  };

  mcpServers: Record<string, { sseUrl: string }>;

  loop: {
    intervalSec: number;
    objective: string;

    observe: Array<{
      server: string;
      tool: string;
      args?: Record<string, any>;
      label?: string;
      maxChars?: number;
      timeoutMs?: number;
    }>;

    act?: {
      enabled?: boolean;
      server: string;
      tool: string;
      dryRun?: boolean;
      timeoutMs?: number;
    };

    actionLog?: string;
    tickTimeoutMs?: number;
    failureBackoffSec?: number;
  };
};

export function loadConfig(path: string): LoopConfig {
  const raw = fs.readFileSync(path, "utf-8");
  const data = yaml.load(raw);
  if (!data || typeof data !== "object") {
    throw new Error(`Invalid YAML config: ${path}`);
  }
  return data as LoopConfig;
}
