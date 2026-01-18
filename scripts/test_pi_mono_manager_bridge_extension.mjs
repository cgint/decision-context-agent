#!/usr/bin/env node

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

function repoRoot() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "..");
}

function buildExtension(entryTs) {
  const root = repoRoot();
  const esbuildBin = path.resolve(root, "../../dev-external/pi-mono/node_modules/.bin/esbuild");
  const outDir = mkdtempSync(path.join(tmpdir(), "dca-pi-ext-test-"));
  const outFile = path.join(outDir, "manager_bridge_ext.mjs");

  execFileSync(esbuildBin, [entryTs, "--platform=node", "--format=esm", "--bundle", `--outfile=${outFile}`], {
    stdio: "inherit",
  });
  return outFile;
}

async function run() {
  const root = repoRoot();
  const extensionTs = path.join(root, "integrations/pi_mono/extensions/manager_bridge/index.ts");
  const built = buildExtension(extensionTs);

  const mod = await import(pathToFileURL(built).href);
  assert.equal(typeof mod.default, "function");

  const registered = new Map();
  const sent = [];
  const flagValues = new Map([
    ["manager-url", "http://manager.local"],
    ["manager-timeout-ms", "800"],
    ["manager-steer-policy", "always"],
  ]);

  const pi = {
    registerFlag(_name, _opts) {},
    getFlag(name) {
      return flagValues.get(name);
    },
    on(event, handler) {
      registered.set(event, handler);
    },
    appendEntry(_customType, _data) {},
    sendMessage(message, options) {
      sent.push({ kind: "sendMessage", payload: message, options });
    },
    sendUserMessage(_content, _options) {
      sent.push({ kind: "sendUserMessage", payload: _content, options: _options });
    },
  };

  // Fake manager response
  let fetchCalls = 0;
  globalThis.fetch = async (_url, _opts) => {
    fetchCalls += 1;
    return {
      status: 200,
      async text() {
        return JSON.stringify({
          ok: true,
          actions: { steer: "STEER: do the next right thing" },
          timing: { duration_ms: 12 },
        });
      },
    };
  };

  mod.default(pi);
  assert.ok(registered.has("turn_end"), "expected turn_end handler");

  const baseCtx = {
    cwd: "/tmp",
    hasUI: false,
    ui: { setStatus(_k, _t) {} },
    sessionManager: { getBranch() { return []; } },
    isIdle() { return false; },
    hasPendingMessages() { return false; },
  };

  const turnEnd = registered.get("turn_end");

  // Case 1: streaming (isIdle=false) -> deliverAs "steer", uses sendMessage (not sendUserMessage)
  sent.length = 0;
  await turnEnd({ turnIndex: 1, message: { role: "assistant", content: "hi" }, toolResults: [] }, baseCtx);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, "sendMessage");
  assert.equal(sent[0].options?.deliverAs, "steer");

  // Case 2: idle (isIdle=true) -> deliverAs "nextTurn"
  sent.length = 0;
  const idleCtx = { ...baseCtx, isIdle() { return true; } };
  await turnEnd({ turnIndex: 2, message: { role: "assistant", content: "hi" }, toolResults: [] }, idleCtx);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, "sendMessage");
  assert.equal(sent[0].options?.deliverAs, "nextTurn");

  // Case 3: duplicate same turn -> no second send
  sent.length = 0;
  await turnEnd({ turnIndex: 3, message: { role: "assistant", content: "hi" }, toolResults: [] }, baseCtx);
  await turnEnd({ turnIndex: 3, message: { role: "assistant", content: "hi" }, toolResults: [] }, baseCtx);
  assert.equal(sent.length, 1);

  // Case 4: manager-events excludes turn_end -> no send, no fetch
  const registered2 = new Map();
  const sent2 = [];
  const flagValues2 = new Map([
    ["manager-url", "http://manager.local"],
    ["manager-timeout-ms", "800"],
    ["manager-events", "tool_call"],
  ]);
  const pi2 = {
    registerFlag(_name, _opts) {},
    getFlag(name) {
      return flagValues2.get(name);
    },
    on(event, handler) {
      registered2.set(event, handler);
    },
    appendEntry(_customType, _data) {},
    sendMessage(message, options) {
      sent2.push({ kind: "sendMessage", payload: message, options });
    },
    sendUserMessage(_content, _options) {
      sent2.push({ kind: "sendUserMessage", payload: _content, options: _options });
    },
  };
  fetchCalls = 0;
  mod.default(pi2);
  assert.ok(registered2.has("turn_end"), "expected turn_end handler (even if disabled)");
  const turnEnd2 = registered2.get("turn_end");
  await turnEnd2({ turnIndex: 1, message: { role: "assistant", content: "hi" }, toolResults: [] }, baseCtx);
  assert.equal(sent2.length, 0);
  assert.equal(fetchCalls, 0);

  // Case 5: default steer policy (on_error) does not inject on ok turn_end
  const registered3 = new Map();
  const sent3 = [];
  const flagValues3 = new Map([
    ["manager-url", "http://manager.local"],
    ["manager-timeout-ms", "800"],
    ["manager-events", "turn_end"],
  ]);
  const pi3 = {
    registerFlag(_name, _opts) {},
    getFlag(name) {
      return flagValues3.get(name);
    },
    on(event, handler) {
      registered3.set(event, handler);
    },
    appendEntry(_customType, _data) {},
    sendMessage(message, options) {
      sent3.push({ kind: "sendMessage", payload: message, options });
    },
    sendUserMessage(_content, _options) {
      sent3.push({ kind: "sendUserMessage", payload: _content, options: _options });
    },
  };
  fetchCalls = 0;
  mod.default(pi3);
  const turnEnd3 = registered3.get("turn_end");
  await turnEnd3({ turnIndex: 1, message: { role: "assistant", content: "hi" }, toolResults: [] }, baseCtx);
  // Allow fire-and-forget postEvent() to run.
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(sent3.length, 0);
  assert.equal(fetchCalls, 1);

  // Case 6: status includes server timing when UI is available
  let lastStatus;
  const uiCtx = {
    ...baseCtx,
    hasUI: true,
    ui: {
      setStatus(_k, text) {
        lastStatus = text;
      },
    },
  };
  sent.length = 0;
  lastStatus = undefined;
  await turnEnd({ turnIndex: 10, message: { role: "assistant", content: "hi" }, toolResults: [] }, uiCtx);
  assert.equal(sent.length, 1);
  assert.ok(String(lastStatus ?? "").includes("server 12ms"));

  console.log("✓ test_pi_mono_manager_bridge_extension passed");
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
