"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

require("./account-menu.js");

const availableResetCount = globalThis.codexMuxAvailableResetCount;
const bindingWindow = globalThis.codexMuxBindingWindow;

test("reset count preserves an explicit available_count", () => {
  assert.equal(
    availableResetCount({
      available_count: 0,
      applicable_available_count: 1,
    }),
    0,
  );
  assert.equal(
    availableResetCount({
      available_count: 1,
      credits: [{ status: "available" }, { status: "consumed" }],
    }),
    1,
  );
});

test("reset count falls back only when available_count is absent", () => {
  assert.equal(availableResetCount({ applicable_available_count: 1 }), 1);
  assert.equal(
    availableResetCount({
      available_count: -1,
      applicable_available_count: 1,
    }),
    null,
  );
  assert.equal(availableResetCount({ available_count: "1" }), null);
  assert.equal(availableResetCount({ credits: [{ status: "available" }] }), null);
  assert.equal(availableResetCount(null), null);
});

test("plugin selection scopes both legacy renderer aliases and native RPC methods", () => {
  globalThis.__codexMuxPluginAccountId = "secondary-test";
  try {
    for (const method of ["list-apps", "list-installed-apps", "read-apps", "list-mcp-server-status", "login-mcp-server", "app/list", "app/installed", "app/read", "mcpServerStatus/list", "mcpServer/oauth/login"]) {
      const params = { cursor: null };
      assert.deepEqual(globalThis.codexMuxScopePluginRequest(method, params), { cursor: null, codexMuxAccountId: "secondary-test" }, method);
      assert.deepEqual(params, { cursor: null }, "input must not be mutated");
    }
    const turn = { threadId: "test-thread" };
    assert.equal(globalThis.codexMuxScopePluginRequest("turn/start", turn), turn);
  } finally {
    delete globalThis.__codexMuxPluginAccountId;
  }
});

test("the reported window is the one that actually blocks a request", () => {
  // The shape that showed "68%" next to an account that could not take a
  // single request: its five-hour window was spent while the week was not.
  const account = {
    primary: { usedPercent: 100, windowDurationMins: 300 },
    secondary: { usedPercent: 32, windowDurationMins: 10080 },
  };
  assert.equal(bindingWindow(account).usedPercent, 100);
  assert.equal(bindingWindow(account).windowDurationMins, 300);
});

test("a spent week still binds when the short window is free", () => {
  const account = {
    primary: { usedPercent: 0, windowDurationMins: 300 },
    secondary: { usedPercent: 100, windowDurationMins: 10080 },
  };
  assert.equal(bindingWindow(account).usedPercent, 100);
  assert.equal(bindingWindow(account).windowDurationMins, 10080);
});

test("an account with headroom everywhere reports its worst window", () => {
  const account = {
    primary: { usedPercent: 0, windowDurationMins: 300 },
    secondary: { usedPercent: 55, windowDurationMins: 10080 },
  };
  assert.equal(bindingWindow(account).usedPercent, 55);
});

test("missing windows do not invent a limit", () => {
  assert.equal(bindingWindow(null), null);
  assert.equal(bindingWindow({}), null);
  assert.equal(
    bindingWindow({ primary: { usedPercent: 12, windowDurationMins: 300 } })
      .usedPercent,
    12,
  );
});

test("an account with a rejected sign-in is not usable", () => {
  const base = { enabled: true, connected: true, authType: "chatgpt" };
  assert.equal(globalThis.codexMuxIsUsable(base), true);
  assert.equal(globalThis.codexMuxIsUsable({ ...base, needsReauth: true }), false);
  assert.equal(globalThis.codexMuxIsUsable({ ...base, enabled: false }), false);
  assert.equal(globalThis.codexMuxIsUsable({ ...base, connected: false }), false);
  assert.equal(globalThis.codexMuxIsUsable({ ...base, authType: "apiKey" }), false);
});

test("credits are shown only when the account has some", () => {
  const text = globalThis.codexMuxCreditsText;
  assert.equal(text(null), null);
  assert.equal(text({ hasCredits: false, unlimited: false, balance: "0" }), null);
  assert.equal(text({ hasCredits: true, unlimited: false, balance: "42.5" }), "42.5 credits");
  assert.equal(text({ hasCredits: true, unlimited: false, balance: "1" }), "1 credit");
  assert.equal(text({ hasCredits: true, unlimited: true, balance: null }), "Unlimited credits");
  assert.equal(text({ hasCredits: true, unlimited: false }), "Credits available");
});

test("usage details list the short window first and include credits", () => {
  const details = globalThis.codexMuxUsageDetails({
    rateLimits: {
      primary: { usedPercent: 100, windowDurationMins: 10080, resetsAt: null },
      secondary: { usedPercent: 25, windowDurationMins: 300, resetsAt: null },
    },
    credits: { hasCredits: true, unlimited: false, balance: "3" },
  });
  assert.equal(details, "5-hour: 75% left · Weekly: 0% left · 3 credits");
  assert.match(
    globalThis.codexMuxUsageDetails({ needsReauth: true }),
    /Sign in again/,
  );
  assert.equal(globalThis.codexMuxUsageDetails({}), "Usage unavailable");
});


test("reset labels distinguish pending, failed and known counts", () => {
  const label = globalThis.codexMuxResetCountText;
  assert.equal(label(undefined), "Loading resets…");
  assert.equal(label(null), "Couldn’t load resets");
  assert.equal(label(0), "0 resets available");
  assert.equal(label(1), "1 reset available");
  assert.equal(label(3), "3 resets available");
});

test("picker and native sheet share pending reset requests per account", async () => {
  const originalFetch = globalThis.fetch;
  const pending = [];
  globalThis.fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }));
  try {
    const primary = globalThis.codexMuxRateLimitResets("reset-test-primary");
    const nativeSheet = globalThis.codexMuxRateLimitResets("reset-test-primary");
    const other = globalThis.codexMuxRateLimitResets("reset-test-secondary");
    assert.equal(primary, nativeSheet);
    assert.equal(pending.length, 2);
    pending[0].resolve({ ok: true, json: async () => ({ available_count: 3 }) });
    pending[1].resolve({ ok: true, json: async () => ({ available_count: 1 }) });
    assert.equal(globalThis.codexMuxAvailableResetCount(await primary), 3);
    assert.equal(globalThis.codexMuxAvailableResetCount(await nativeSheet), 3);
    assert.equal(globalThis.codexMuxAvailableResetCount(await other), 1);
    const refreshed = globalThis.codexMuxRateLimitResets("reset-test-primary");
    assert.equal(pending.length, 3, "settled requests must allow fresh balances");
    pending[2].resolve({ ok: true, json: async () => ({ available_count: 2 }) });
    assert.equal(globalThis.codexMuxAvailableResetCount(await refreshed), 2);
  } finally { globalThis.fetch = originalFetch; }
});
