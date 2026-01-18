import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { request as httpRequest } from "node:http";
import { dirname, resolve as pathResolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

type ManagerBridgeResponse = {
  ok?: boolean;
  actions?: {
    block?: boolean;
    reason?: string;
    steer?: string;
  };
  timing?: {
    started_at?: string;
    ended_at?: string;
    duration_ms?: number;
  };
};

const CUSTOM_SESSION_ENTRY = "dca_manager_bridge_session";
const STATUS_KEY = "dca-manager-bridge";
const STEER_MESSAGE_TYPE = "dca_manager_bridge_steer";
const STDIO_URL_PREFIX = "stdio:";

function truncate(text: string, limit: number): string {
  const value = (text ?? "").trim();
  if (value.length <= limit) return value;
  if (limit <= 3) return value.slice(0, limit);
  return value.slice(0, limit - 3) + "...";
}

function setStatus(ctx: ExtensionContext, text: string | undefined): void {
  if (!ctx.hasUI) return;
  ctx.ui.setStatus(STATUS_KEY, text);
}

function isUnixManagerUrl(url: string): boolean {
  return (url ?? "").trim().startsWith("unix:");
}

function isStdioManagerUrl(url: string): boolean {
  return (url ?? "").trim().startsWith(STDIO_URL_PREFIX);
}

function unixSocketPathFromUrl(url: string): string {
  let path = (url ?? "").trim().slice("unix:".length);
  if (path.startsWith("//")) path = path.slice(2);
  return path;
}

function getLogDir(pi: ExtensionAPI): string {
  const flag = pi.getFlag("manager-log-dir");
  if (typeof flag === "string" && flag.trim()) return flag.trim();
  const env = process.env.DCA_MANAGER_LOG_DIR;
  return env && env.trim() ? env.trim() : "data/pi_mono_bridge";
}

function getManagerPython(pi: ExtensionAPI): string {
  const flag = pi.getFlag("manager-python");
  if (typeof flag === "string" && flag.trim()) return flag.trim();
  const env = process.env.DCA_MANAGER_PYTHON;
  return env && env.trim() ? env.trim() : "python3";
}

function getManagerScriptPath(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  return pathResolve(here, "../../../../scripts/pi_mono_manager_bridge_server.py");
}

class StdioManagerClient {
  private proc: ChildProcessWithoutNullStreams;
  private rl: ReturnType<typeof createInterface>;
  private queue: Array<{
    resolve: (value: ManagerBridgeResponse | undefined) => void;
    reject: (error: Error) => void;
    timeout: NodeJS.Timeout;
  }> = [];
  private closed = false;

  constructor(command: string, args: string[], cwd: string | undefined) {
    this.proc = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
      env: process.env,
    });

    this.rl = createInterface({ input: this.proc.stdout });

    this.rl.on("line", (line) => {
      const next = this.queue.shift();
      if (!next) return;
      clearTimeout(next.timeout);
      try {
        const parsed = JSON.parse(line) as ManagerBridgeResponse;
        next.resolve(parsed);
      } catch {
        next.resolve({});
      }
    });

    const failAll = (error: Error) => {
      if (this.closed) return;
      this.closed = true;
      for (const item of this.queue) {
        clearTimeout(item.timeout);
        item.reject(error);
      }
      this.queue = [];
    };

    this.proc.on("error", (err) => failAll(err instanceof Error ? err : new Error(String(err))));
    this.proc.on("exit", (code, signal) =>
      failAll(new Error(`manager-bridge exited (code=${code ?? "?"}, signal=${signal ?? "?"})`)),
    );
  }

  async request(payload: unknown, timeoutMs: number): Promise<ManagerBridgeResponse | undefined> {
    if (this.closed) return undefined;
    const body = JSON.stringify(payload);
    return await new Promise<ManagerBridgeResponse | undefined>((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("manager-bridge stdio timeout"));
      }, timeoutMs);
      this.queue.push({ resolve, reject, timeout });
      this.proc.stdin.write(body + "\n");
    });
  }
}

function safeStringify(value: unknown, limitBytes: number): string {
  const seen = new WeakSet();
  const json = JSON.stringify(
    value,
    (_key, val) => {
      if (typeof val === "object" && val !== null) {
        if (seen.has(val as object)) return "[Circular]";
        seen.add(val as object);
      }
      return val;
    },
    2,
  );
  return truncate(json, limitBytes);
}

function contentToText(content: any): string {
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const block of content) {
    if (!block) continue;
    if (typeof block === "string") {
      parts.push(block);
      continue;
    }
    if (block.type === "text" && typeof block.text === "string") {
      parts.push(block.text);
    }
  }
  return parts.join("\n").trim();
}

function messageToText(message: any): string {
  if (!message) return "";
  if (typeof message === "string") return message.trim();
  if (typeof message.content === "string") return message.content.trim();
  if (Array.isArray(message.content)) return contentToText(message.content);
  if (Array.isArray(message.blocks)) return contentToText(message.blocks);
  return safeStringify(message, 4_000);
}

function summarizeToolResults(toolResults: any[]): { summary: string; hadError: boolean } {
  if (!Array.isArray(toolResults) || toolResults.length === 0) {
    return { summary: "", hadError: false };
  }
  const lines: string[] = [];
  let hadError = false;
  for (const tr of toolResults) {
    const toolName = tr?.toolName ?? tr?.name ?? tr?.tool ?? "tool";
    const isError = Boolean(tr?.isError ?? tr?.error ?? false);
    if (isError) hadError = true;
    const contentText = contentToText(tr?.content ?? tr?.result?.content ?? []);
    const head = truncate(contentText, 400);
    lines.push(`- ${toolName}${isError ? " (error)" : ""}: ${head || "(no text)"}`);
  }
  return { summary: truncate(lines.join("\n"), 4_000), hadError };
}

function getManagerUrl(pi: ExtensionAPI): string | undefined {
  const flagUrl = pi.getFlag("manager-url");
  if (typeof flagUrl === "string" && flagUrl.trim()) return flagUrl.trim();
  const envUrl = process.env.DCA_MANAGER_URL ?? process.env.PI_MANAGER_URL;
  return envUrl && envUrl.trim() ? envUrl.trim() : undefined;
}

function getTimeoutMs(pi: ExtensionAPI): number {
  const flag = pi.getFlag("manager-timeout-ms");
  if (typeof flag === "string") {
    const parsed = Number.parseInt(flag, 10);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return 800;
}

function parseEnabledEvents(pi: ExtensionAPI): Set<string> {
  const flag = pi.getFlag("manager-events");
  const raw = typeof flag === "string" ? flag : "";
  const value = raw.trim().toLowerCase();
  if (!value) {
    return new Set(["session_start", "before_agent_start", "tool_call", "turn_end"]);
  }
  if (value === "all") {
    return new Set(["session_start", "before_agent_start", "tool_call", "tool_result", "turn_end"]);
  }
  const items = value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
  return new Set(items);
}

function parseSteerPolicy(pi: ExtensionAPI): "off" | "on_error" | "always" {
  const flag = pi.getFlag("manager-steer-policy");
  const raw = typeof flag === "string" ? flag.trim().toLowerCase() : "";
  if (raw === "off" || raw === "on_error" || raw === "always") return raw;
  return "on_error";
}

function reconstructSessionId(ctx: ExtensionContext): string | undefined {
  try {
    const branch = (ctx.sessionManager as any).getBranch?.();
    if (!Array.isArray(branch)) return undefined;
    for (let i = branch.length - 1; i >= 0; i -= 1) {
      const entry = branch[i];
      if (entry?.type === "custom" && entry?.customType === CUSTOM_SESSION_ENTRY) {
        const sid = entry?.data?.session_id;
        if (typeof sid === "string" && sid.trim()) return sid.trim();
      }
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function newSessionId(): string {
  const cryptoObj: any = (globalThis as any).crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  return `dca-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function postEvent(
  pi: ExtensionAPI,
  managerUrl: string,
  timeoutMs: number,
  payload: unknown,
  ctx: ExtensionContext,
): Promise<ManagerBridgeResponse | undefined> {
  const now = Date.now();
  const state = (postEvent as any)._state as
    | {
        consecutiveFailures: number;
        backoffUntilMs: number;
        lastLatencyMs?: number;
        lastOkAtMs?: number;
      }
    | undefined;
  const s =
    state ??
    ((postEvent as any)._state = {
      consecutiveFailures: 0,
      backoffUntilMs: 0,
      lastLatencyMs: undefined,
      lastOkAtMs: undefined,
    });

  if (now < s.backoffUntilMs) {
    setStatus(ctx, `manager: backoff (${Math.ceil((s.backoffUntilMs - now) / 1000)}s)`);
    return undefined;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const durationMsStart = Date.now();
    const durationMsStop = () => Date.now() - durationMsStart;

    let statusCode = 0;
    let bodyText = "";

    if (isStdioManagerUrl(managerUrl)) {
      const existing = (postEvent as any)._stdioClient as StdioManagerClient | undefined;
      const client =
        existing ??
        ((postEvent as any)._stdioClient = new StdioManagerClient(
          getManagerPython(pi),
          [getManagerScriptPath(), "--stdio", "--log-dir", getLogDir(pi)],
          ctx.cwd,
        ));

      try {
        const data = await client.request(payload, timeoutMs);
        const durationMs = durationMsStop();
        s.consecutiveFailures = 0;
        s.backoffUntilMs = 0;
        s.lastLatencyMs = durationMs;
        s.lastOkAtMs = Date.now();
        const serverMs = data?.timing?.duration_ms;
        if (typeof serverMs === "number") {
          setStatus(ctx, `manager: ok (${durationMs}ms, server ${serverMs}ms)`);
        } else {
          setStatus(ctx, `manager: ok (${durationMs}ms)`);
        }
        return data;
      } catch {
        const durationMs = durationMsStop();
        s.consecutiveFailures += 1;
        const backoffMs = Math.min(15_000, 500 * 2 ** Math.min(6, s.consecutiveFailures));
        s.backoffUntilMs = Date.now() + backoffMs;
        setStatus(ctx, `manager: offline (backoff ${Math.ceil(backoffMs / 1000)}s)`);
        s.lastLatencyMs = durationMs;
        return undefined;
      }
    }

    if (isUnixManagerUrl(managerUrl)) {
      const socketPath = unixSocketPathFromUrl(managerUrl);
      if (!socketPath) return undefined;

      const body = JSON.stringify(payload);
      const { statusCode: code, body: text } = await new Promise<{ statusCode: number; body: string }>(
        (resolve, reject) => {
          const req = httpRequest(
            {
              socketPath,
              path: "/v1/event",
              method: "POST",
              headers: {
                "content-type": "application/json",
                "content-length": String(Buffer.byteLength(body)),
              },
            },
            (res) => {
              const chunks: string[] = [];
              res.setEncoding("utf8");
              res.on("data", (chunk) => chunks.push(String(chunk)));
              res.on("end", () =>
                resolve({ statusCode: res.statusCode ?? 0, body: chunks.join("") }),
              );
            },
          );

          const onAbort = () => req.destroy(new Error("aborted"));
          if (controller.signal.aborted) onAbort();
          else controller.signal.addEventListener("abort", onAbort, { once: true });

          req.on("error", reject);
          req.end(body);
        },
      );
      statusCode = code;
      bodyText = text;
    } else {
      const response = await fetch(`${managerUrl.replace(/\/$/, "")}/v1/event`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      statusCode = response.status;
      bodyText = await response.text();
    }

    const durationMs = durationMsStop();
    if (statusCode < 200 || statusCode >= 300) {
      s.consecutiveFailures += 1;
      const backoffMs = Math.min(15_000, 500 * 2 ** Math.min(6, s.consecutiveFailures));
      s.backoffUntilMs = Date.now() + backoffMs;
      setStatus(ctx, `manager: http ${statusCode} (backoff ${Math.ceil(backoffMs / 1000)}s)`);
      return undefined;
    }
    let data: ManagerBridgeResponse;
    try {
      data = JSON.parse(bodyText) as ManagerBridgeResponse;
    } catch {
      data = {};
    }
    s.consecutiveFailures = 0;
    s.backoffUntilMs = 0;
    s.lastLatencyMs = durationMs;
    s.lastOkAtMs = Date.now();
    const serverMs = data.timing?.duration_ms;
    if (typeof serverMs === "number") {
      setStatus(ctx, `manager: ok (${durationMs}ms, server ${serverMs}ms)`);
    } else {
      setStatus(ctx, `manager: ok (${durationMs}ms)`);
    }
    return data;
  } catch {
    s.consecutiveFailures += 1;
    const backoffMs = Math.min(15_000, 500 * 2 ** Math.min(6, s.consecutiveFailures));
    s.backoffUntilMs = Date.now() + backoffMs;
    setStatus(ctx, `manager: offline (backoff ${Math.ceil(backoffMs / 1000)}s)`);
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

export default function (pi: ExtensionAPI) {
  pi.registerFlag("manager-url", {
    description: "Decision Context Agent manager-bridge base URL (e.g. http://127.0.0.1:8787)",
    type: "string",
  });
  pi.registerFlag("manager-timeout-ms", {
    description: "Timeout for manager-bridge HTTP requests (ms)",
    type: "string",
    default: "800",
  });
  pi.registerFlag("manager-log-dir", {
    description: "Manager-bridge log directory (used for stdio-spawned manager-bridge)",
    type: "string",
  });
  pi.registerFlag("manager-python", {
    description: "Python executable used to spawn the manager-bridge in stdio mode (default: python3)",
    type: "string",
  });
  pi.registerFlag("manager-events", {
    description:
      'Comma-separated event list to forward: session_start,before_agent_start,tool_call,tool_result,turn_end or "all". Default is minimal.',
    type: "string",
  });
  pi.registerFlag("manager-steer-policy", {
    description:
      'When to await manager for steering: "off" | "on_error" | "always". Default is "on_error".',
    type: "string",
  });

  const enabledEvents = parseEnabledEvents(pi);
  const steerPolicy = parseSteerPolicy(pi);
  let sessionId: string | undefined;
  let lastSteerTurnIndex: number | undefined;

  function ensureSession(ctx: ExtensionContext): string | undefined {
    if (sessionId) return sessionId;
    sessionId = reconstructSessionId(ctx) ?? newSessionId();
    pi.appendEntry(CUSTOM_SESSION_ENTRY, { session_id: sessionId });
    return sessionId;
  }

  pi.on("session_start", async (_event, ctx) => {
    const managerUrl = getManagerUrl(pi);
    if (!managerUrl) return;
    if (!enabledEvents.has("session_start")) return;
    const sid = ensureSession(ctx);
    if (!sid) return;
    const timeoutMs = getTimeoutMs(pi);
    void postEvent(
      pi,
      managerUrl,
      timeoutMs,
      {
        session_id: sid,
        event: { type: "session_start", timestamp: Date.now(), cwd: ctx.cwd },
      },
      ctx,
    );
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const managerUrl = getManagerUrl(pi);
    if (!managerUrl) return;
    if (!enabledEvents.has("before_agent_start")) return;
    const sid = ensureSession(ctx);
    if (!sid) return;
    const timeoutMs = getTimeoutMs(pi);
    void postEvent(
      pi,
      managerUrl,
      timeoutMs,
      {
        session_id: sid,
        event: {
          type: "before_agent_start",
          timestamp: Date.now(),
          prompt: truncate(String(event.prompt ?? ""), 8_000),
          system_prompt: truncate(String(event.systemPrompt ?? ""), 8_000),
          cwd: ctx.cwd,
        },
      },
      ctx,
    );
  });

  pi.on("tool_call", async (event, ctx) => {
    const managerUrl = getManagerUrl(pi);
    if (!managerUrl) return;
    if (!enabledEvents.has("tool_call")) return;
    const sid = ensureSession(ctx);
    if (!sid) return;
    const timeoutMs = getTimeoutMs(pi);
    const response = await postEvent(
      pi,
      managerUrl,
      timeoutMs,
      {
        session_id: sid,
        event: {
          type: "tool_call",
          timestamp: Date.now(),
          tool_name: event.toolName,
          tool_call_id: event.toolCallId,
          input: event.input,
          cwd: ctx.cwd,
        },
      },
      ctx,
    );
    if (response?.actions?.block) {
      return {
        block: true,
        reason: response.actions.reason ?? "Blocked by manager-bridge",
      };
    }
  });

  pi.on("tool_result", async (event, ctx) => {
    const managerUrl = getManagerUrl(pi);
    if (!managerUrl) return;
    if (!enabledEvents.has("tool_result")) return;
    const sid = ensureSession(ctx);
    if (!sid) return;
    const timeoutMs = getTimeoutMs(pi);
    void postEvent(
      pi,
      managerUrl,
      timeoutMs,
      {
        session_id: sid,
        event: {
          type: "tool_result",
          timestamp: Date.now(),
          tool_name: (event as any).toolName,
          tool_call_id: (event as any).toolCallId,
          input: (event as any).input,
          is_error: Boolean((event as any).isError),
          content_text: truncate(contentToText((event as any).content), 8_000),
          details: (event as any).details,
          cwd: ctx.cwd,
        },
      },
      ctx,
    );
  });

  pi.on("turn_end", async (event, ctx) => {
    const managerUrl = getManagerUrl(pi);
    if (!managerUrl) return;
    if (!enabledEvents.has("turn_end")) return;
    const sid = ensureSession(ctx);
    if (!sid) return;
    const timeoutMs = getTimeoutMs(pi);

    const { summary, hadError } = summarizeToolResults((event as any).toolResults ?? []);
    const turnIndex = (event as any).turnIndex;
    const payload = {
      session_id: sid,
      event: {
        type: "turn_end",
        timestamp: Date.now(),
        turn_index: turnIndex,
        assistant_text: truncate(messageToText((event as any).message), 8_000),
        tool_results_summary: summary,
        had_error: hadError,
        cwd: ctx.cwd,
      },
    };

    if (steerPolicy === "off" || (steerPolicy === "on_error" && !hadError)) {
      void postEvent(pi, managerUrl, timeoutMs, payload, ctx);
      return;
    }

    const response = await postEvent(pi, managerUrl, timeoutMs, payload, ctx);

    const steer = response?.actions?.steer;
    if (!steer) return;
    if (ctx.hasPendingMessages()) return;
    if (typeof turnIndex === "number" && lastSteerTurnIndex === turnIndex) return;

    const deliverAs = ctx.isIdle() ? "nextTurn" : "steer";
    pi.sendMessage(
      {
        customType: STEER_MESSAGE_TYPE,
        content: steer,
        display: true,
        details: { turnIndex: typeof turnIndex === "number" ? turnIndex : undefined },
      },
      { deliverAs },
    );
    if (typeof turnIndex === "number") lastSteerTurnIndex = turnIndex;
  });
}
