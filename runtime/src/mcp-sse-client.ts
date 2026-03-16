type Pending = {
  resolve: (v: any) => void;
  reject: (e: any) => void;
};

export class McpSseClient {
  private sseUrl: string;
  private messageUrl: string | null = null;
  private abortController: AbortController | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private readerPromise: Promise<void> | null = null;

  constructor(sseUrl: string) {
    this.sseUrl = sseUrl;
  }

  isReady(): boolean {
    return !!this.messageUrl;
  }

  async connect(signal?: AbortSignal): Promise<void> {
    if (this.readerPromise) return;

    this.abortController = new AbortController();
    if (signal) {
      signal.addEventListener("abort", () => this.abortController?.abort(), { once: true });
    }

    this.readerPromise = this._readLoop(this.abortController.signal).catch((err) => {
      for (const [id, p] of this.pending) {
        p.reject(err);
        this.pending.delete(id);
      }
      this.messageUrl = null;
      this.readerPromise = null;
    });

    const startedAt = Date.now();
    while (!this.messageUrl) {
      if (this.abortController.signal.aborted) throw new Error("MCP connect aborted");
      if (Date.now() - startedAt > 10_000) throw new Error("MCP connect timeout (no endpoint event)");
      await new Promise((r) => setTimeout(r, 50));
    }

    await this.request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "mcp-test-loop-runtime", version: "0.1.0" },
    });

    try {
      await this.notify("notifications/initialized", {});
    } catch {
      // ignore
    }
  }

  async close(): Promise<void> {
    this.abortController?.abort();
    this.abortController = null;
    this.messageUrl = null;
    this.readerPromise = null;
  }

  async request(method: string, params?: any, options?: { timeoutMs?: number }): Promise<any> {
    if (!this.messageUrl) throw new Error("MCP not connected (no message endpoint)");

    const id = this.nextId++;
    const body = { jsonrpc: "2.0", id, method, params };

    const p = new Promise<any>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });

    const controller = new AbortController();
    const timeoutMs = options?.timeoutMs ?? 10_000;
    const t = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const res = await fetch(this.messageUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`MCP POST failed: HTTP ${res.status} ${res.statusText} ${text}`);
      }
    } finally {
      clearTimeout(t);
    }

    return await p;
  }

  async notify(method: string, params?: any): Promise<void> {
    if (!this.messageUrl) throw new Error("MCP not connected (no message endpoint)");

    const body = { jsonrpc: "2.0", method, params };
    const res = await fetch(this.messageUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`MCP notify failed: HTTP ${res.status} ${res.statusText} ${text}`);
    }
  }

  async callTool(name: string, args?: Record<string, any>, options?: { timeoutMs?: number }): Promise<any> {
    return await this.request(
      "tools/call",
      { name, arguments: args ?? {} },
      { timeoutMs: options?.timeoutMs },
    );
  }

  private async _readLoop(signal: AbortSignal): Promise<void> {
    const res = await fetch(this.sseUrl, {
      method: "GET",
      headers: { accept: "text/event-stream" },
      signal,
    });

    if (!res.ok || !res.body) {
      throw new Error(`MCP SSE connect failed: HTTP ${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");

    let buf = "";
    let currentEvent: string | null = null;
    let currentData: string[] = [];

    const flush = () => {
      if (currentEvent === null && currentData.length === 0) return;
      const ev = currentEvent ?? "message";
      const data = currentData.join("\n");
      currentEvent = null;
      currentData = [];
      this._handleSseEvent(ev, data);
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      while (true) {
        const idx = buf.indexOf("\n");
        if (idx === -1) break;
        let line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);
        line = line.replace(/\r$/, "");

        if (line === "") {
          flush();
          continue;
        }

        if (line.startsWith(":")) continue;

        if (line.startsWith("event:")) {
          currentEvent = line.slice("event:".length).trim();
          continue;
        }

        if (line.startsWith("data:")) {
          currentData.push(line.slice("data:".length).trim());
          continue;
        }
      }
    }

    flush();
  }

  private _handleSseEvent(event: string, data: string) {
    if (event === "endpoint") {
      const endpoint = data.trim();
      try {
        const parsed = JSON.parse(endpoint);
        if (typeof parsed?.endpoint === "string") {
          this.messageUrl = new URL(parsed.endpoint, this.sseUrl).toString();
          return;
        }
      } catch {
        // ignore
      }
      this.messageUrl = new URL(endpoint, this.sseUrl).toString();
      return;
    }

    try {
      const msg = JSON.parse(data);
      if (typeof msg?.id === "number" && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id)!;
        this.pending.delete(msg.id);
        if (msg.error) {
          p.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        } else {
          p.resolve(msg.result);
        }
      }
    } catch {
      // ignore
    }
  }
}
