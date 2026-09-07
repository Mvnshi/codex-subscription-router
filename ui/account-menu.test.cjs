"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

require("./account-menu.js");

const availableResetCount = globalThis.codexMuxAvailableResetCount;

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
