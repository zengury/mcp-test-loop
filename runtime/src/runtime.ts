import { Agent } from "@mariozechner/pi-agent-core";
import { getModel, type Model } from "@mariozechner/pi-ai";
import fs from "node:fs";
import path from "node:path";

import type { LoopConfig } from "./config.js";
import { McpSseClient } from "./mcp-sse-client.js";

function nowIso() {
  return new Date().toISOString();
}

function truncate(text: string, maxChars: number) {
  if (text.length <= maxChars) return text;
  return text.slice(0, maxChars) + `\n\n…(truncated ${text.length - maxChars} chars)…`;
}

function extractFirstJsonObject(text: string): any | null {
  const fenced = text.match(/```json\s*([\s\S]*?)\s*```/i);
  const candidate = fenced ? fenced[1] : text;

  const start = candidate.indexOf("{");
  if (start === -1) return null;

  let depth = 0;
  for (let i = start; i < candidate.length; i++) {
    const ch = candidate[i];
    if (ch === "{") depth++;
    if (ch === "}") depth--;
    if (depth === 0) {
      const jsonStr = candidate.slice(start, i + 1);
      try {
        return JSON.parse(jsonStr);
      } catch {
        return null;
      }
    }
  }
  return null;
}

function jsonlAppend(filePath: string, obj: any) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, JSON.stringify(obj) + "\n", "utf-8");
}

export class LoopRuntime {
  private cfg: LoopConfig;
  private agent: Agent;
  private clients = new Map<string, McpSseClient>();

  private running = false;
  private tick = 0;
  private timer: NodeJS.Timeout | null = null;

  constructor(cfg: LoopConfig) {
    this.cfg = cfg;

    const model: Model<any> = getModel(cfg.llm.provider as any, cfg.llm.model as any);

    this.agent = new Agent({
      getApiKey: () => {
        if (!cfg.llm.apiKeyEnv) return undefined;
        return process.env[cfg.llm.apiKeyEnv];
      },
      initialState: {
        model,
        thinkingLevel: cfg.llm.thinkingLevel ?? "off",
        systemPrompt: this.buildSystemPrompt(),
      },
    });

    // After each tick turn completes, schedule next tick (LoopForever)
    this.agent.subscribe((e) => {
      if (e.type === "agent_end" && this.running) {
        this.scheduleNextTick(this.cfg.loop.intervalSec * 1000);
      }
    });
  }

  private buildSystemPrompt(): string {
    return [
      "You are a persistent long-horizon operations agent.",
      "You operate in discrete ticks.",
      "You receive observations collected deterministically.",
      "Always be safe and conservative when uncertain.",
      "Output exactly one JSON object when requested.",
    ].join("\n");
  }

  private getClient(serverName: string): McpSseClient {
    const def = this.cfg.mcpServers[serverName];
    if (!def) throw new Error(`Unknown MCP server '${serverName}'`);
    let c = this.clients.get(serverName);
    if (!c) {
      c = new McpSseClient(def.sseUrl);
      this.clients.set(serverName, c);
    }
    return c;
  }

  private buildDecisionPrompt(observations: Array<{ label: string; text: string }>): string {
    const obsText = observations
      .map((o, i) => `## Observation ${i + 1}: ${o.label}\n${o.text}`)
      .join("\n\n");

    return [
      `[tick] ${nowIso()} #${this.tick}`,
      `Objective: ${this.cfg.loop.objective}`,
      "You must output EXACTLY one JSON object (no extra text) with this schema:",
      "{\n  \"strategy\": string,\n  \"rationale\": string,\n  \"act\": { \"preset\": string } | null\n}",
      "Rules:",
      "- strategy must be one of: CONTINUE, SLOW_DOWN, STOP, HOLD, ADJUST",
      "- If any CRITICAL/fall risk => strategy must be STOP or HOLD",
      "- act.preset must be an allowlisted preset in motion-control; if no action needed set act=null",
      "Observations:",
      obsText || "(none)",
    ].join("\n\n");
  }

  async start() {
    if (this.running) return;
    this.running = true;
    this.tick = 0;
    this.scheduleNextTick(0);
  }

  async stop() {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    for (const c of this.clients.values()) {
      await c.close().catch(() => {});
    }
  }

  async steer(text: string) {
    const msg = {
      role: "user" as const,
      content: [{ type: "text" as const, text }],
      timestamp: Date.now(),
    };

    if (this.agent.state.isStreaming) {
      this.agent.steer(msg);
    } else {
      await this.agent.prompt(msg);
    }
  }

  private scheduleNextTick(delayMs: number) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      const tickTimeoutMs = this.cfg.loop.tickTimeoutMs ?? 60_000;
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), tickTimeoutMs);
      void this.runTick(ac.signal).finally(() => clearTimeout(t));
    }, delayMs);
  }

  private async runTick(signal: AbortSignal) {
    if (!this.running) return;

    const logPath = this.cfg.loop.actionLog ?? "./mcp_loop_actions.jsonl";
    this.tick += 1;

    try {
      // 1) deterministic observe (strict order)
      const observations: Array<{ label: string; text: string; server: string; tool: string }> = [];

      for (const step of this.cfg.loop.observe ?? []) {
        if (signal.aborted) throw new Error("tick timeout");

        const label = step.label ?? `${step.server}.${step.tool}`;
        const client = this.getClient(step.server);

        if (!client.isReady()) {
          const ac = new AbortController();
          const t = setTimeout(() => ac.abort(), 8_000);
          try {
            await client.connect(ac.signal);
          } finally {
            clearTimeout(t);
          }
        }

        const result = await client.callTool(step.tool, step.args ?? {}, { timeoutMs: step.timeoutMs ?? 10_000 });
        const raw = JSON.stringify(result, null, 2);
        const text = truncate(raw, step.maxChars ?? 8000);
        observations.push({ label, text, server: step.server, tool: step.tool });
      }

      // 2) LLM decision (no tools)
      const prompt = this.buildDecisionPrompt(observations.map((o) => ({ label: o.label, text: o.text })));
      if (signal.aborted) throw new Error("tick timeout");

      await this.agent.prompt(prompt);
      if (signal.aborted) throw new Error("tick timeout");

      // 3) parse JSON decision
      const messages = this.agent.state.messages;
      let lastAssistantText = "";
      for (let i = messages.length - 1; i >= 0; i--) {
        const m: any = messages[i];
        if (m?.role === "assistant") {
          const parts = Array.isArray(m.content) ? m.content : [];
          lastAssistantText = parts
            .filter((c: any) => c?.type === "text")
            .map((c: any) => c.text)
            .join("")
            .trim();
          break;
        }
      }

      const decision = extractFirstJsonObject(lastAssistantText) ?? null;

      jsonlAppend(logPath, {
        ts: nowIso(),
        tick: this.tick,
        kind: "decision",
        observations: observations.map((o) => ({ label: o.label, server: o.server, tool: o.tool })),
        decision,
        rawAssistant: lastAssistantText,
      });

      // 4) deterministic act (optional)
      const actCfg = this.cfg.loop.act;
      const actEnabled = actCfg && (actCfg.enabled ?? true);
      const preset = decision?.act?.preset;

      if (actEnabled && typeof preset === "string" && preset.length > 0) {
        const client = this.getClient(actCfg.server);
        if (!client.isReady()) {
          const ac = new AbortController();
          const t = setTimeout(() => ac.abort(), 8_000);
          try {
            await client.connect(ac.signal);
          } finally {
            clearTimeout(t);
          }
        }

        const args: any = { preset };
        if (actCfg.dryRun !== undefined) args.dry_run = actCfg.dryRun;
        if (signal.aborted) throw new Error("tick timeout");

        const actResult = await client.callTool(actCfg.tool, args, { timeoutMs: actCfg.timeoutMs ?? 10_000 });

        jsonlAppend(logPath, {
          ts: nowIso(),
          tick: this.tick,
          kind: "act",
          server: actCfg.server,
          tool: actCfg.tool,
          args,
          result: actResult,
        });
      }
    } catch (err: any) {
      jsonlAppend(logPath, { ts: nowIso(), tick: this.tick, kind: "error", error: err?.message || String(err) });

      if (this.running) {
        const intervalSec = this.cfg.loop.intervalSec;
        const backoffSec = this.cfg.loop.failureBackoffSec ?? Math.min(intervalSec * 2, 300);
        this.scheduleNextTick(backoffSec * 1000);
      }
    }
  }
}
