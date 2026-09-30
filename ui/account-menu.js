const CODEX_MUX_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";
function CodexMuxProfileMenuOpenChange(setOpen) {
  return (nextOpen) => setOpen(nextOpen);
}

async function codexMuxRequest(path, options = {}) {
  const response = await fetch(`${CODEX_MUX_API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Codex-Mux-Token": CODEX_MUX_TOKEN,
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

const CODEX_MUX_ACCOUNTS_CACHE_KEY = "codex-mux.accounts";

function codexMuxCachedAccounts() {
  if (Array.isArray(globalThis.__codexMuxAccounts)) {
    return globalThis.__codexMuxAccounts;
  }
  try {
    const stored = JSON.parse(localStorage.getItem(CODEX_MUX_ACCOUNTS_CACHE_KEY));
    if (Array.isArray(stored)) {
      globalThis.__codexMuxAccounts = stored;
      return stored;
    }
  } catch {}
  return [];
}

function codexMuxRememberAccounts(accounts) {
  globalThis.__codexMuxAccounts = accounts;
  try {
    localStorage.setItem(CODEX_MUX_ACCOUNTS_CACHE_KEY, JSON.stringify(accounts));
  } catch {}
}

async function codexMuxFetchAccounts() {
  const result = await codexMuxRequest("/accounts");
  const accounts = result.accounts || [];
  globalThis.__codexMuxResetPolicy = result.routing?.resetPolicy === "auto" ? "auto" : "ask";
  codexMuxRememberAccounts(accounts);
  return accounts;
}

// An account can take chats only when it is enabled, signed in, and its saved
// sign-in is still accepted by ChatGPT.
function codexMuxIsUsable(account) {
  return Boolean(
    account &&
      account.enabled &&
      account.connected &&
      !account.needsReauth &&
      (!account.authType || account.authType === "chatgpt"),
  );
}

function codexMuxAccountTitle(account) {
  return account.planLabel ? `${account.label} · ${account.planLabel}` : account.label;
}

function codexMuxCreditsText(credits) {
  if (credits == null || typeof credits !== "object") return null;
  if (credits.unlimited) return "Unlimited credits";
  const balance = Number.parseFloat(credits.balance ?? "");
  if (!credits.hasCredits && !(balance > 0)) return null;
  if (!Number.isFinite(balance)) return "Credits available";
  const rounded = Math.round(balance * 100) / 100;
  return `${rounded.toLocaleString()} ${rounded === 1 ? "credit" : "credits"}`;
}

function codexMuxWindowName(window) {
  const minutes = window?.windowDurationMins || 0;
  if (minutes >= 10080) return "Weekly";
  if (minutes >= 1440) return `${Math.round(minutes / 1440)}-day`;
  if (minutes >= 60) return `${Math.round(minutes / 60)}-hour`;
  return minutes > 0 ? `${minutes}-min` : "Usage";
}

function codexMuxResetText(resetsAt, now = Date.now()) {
  if (resetsAt == null) return null;
  const date = new Date(resetsAt * 1000);
  if (Number.isNaN(date.getTime())) return null;
  const sameDay = new Date(now).toDateString() === date.toDateString();
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `resets ${time}`;
  const day = date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return `resets ${day}, ${time}`;
}

// One line per limit window, e.g. "5-hour: 40% left, resets 3:04 PM".
function codexMuxUsageDetails(account, now = Date.now()) {
  if (account?.needsReauth) return "ChatGPT rejected this sign-in. Sign in again to use it.";
  const windows = [account?.rateLimits?.primary, account?.rateLimits?.secondary]
    .filter(Boolean)
    .sort((left, right) => (left.windowDurationMins || 0) - (right.windowDurationMins || 0));
  const lines = windows.map((window) => {
    const left = Math.max(0, Math.round(100 - window.usedPercent));
    const reset = codexMuxResetText(window.resetsAt, now);
    return `${codexMuxWindowName(window)}: ${left}% left${reset ? `, ${reset}` : ""}`;
  });
  const credits = codexMuxCreditsText(account?.credits);
  if (credits) lines.push(credits);
  if (lines.length === 0) return "Usage unavailable";
  return lines.join(" · ");
}

async function codexMuxSetPreferredAccount(accountId) {
  return codexMuxRequest("/routing", {
    method: "PUT",
    body: JSON.stringify({ preferredAccountId: accountId || null }),
  });
}

async function codexMuxUpdateAccount(accountId, changes) {
  return codexMuxRequest(`/accounts/${encodeURIComponent(accountId)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

async function codexMuxRefreshAccount(accountId) {
  return codexMuxRequest(`/accounts/${encodeURIComponent(accountId)}/refresh`, {
    method: "POST",
    body: "{}",
  });
}

async function codexMuxRemoveAccount(accountId) {
  return codexMuxRequest(`/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

const CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS = new Set([
  "list-apps",
  "list-installed-apps",
  "read-apps",
  "list-mcp-server-status",
  "login-mcp-server",
  // Newer desktop builds invoke this hook at the app-server RPC layer.
  "app/list",
  "app/installed",
  "app/read",
  "mcpServerStatus/list",
  "mcpServer/oauth/login",
]);

function codexMuxScopePluginRequest(method, params) {
  const accountId = globalThis.__codexMuxPluginAccountId;
  if (
    !accountId ||
    !CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS.has(method) ||
    (params != null &&
      (typeof params !== "object" || Array.isArray(params)))
  ) {
    return params;
  }
  return { ...(params || {}), codexMuxAccountId: accountId };
}

async function codexMuxProfileData(accountId = null) {
  const query = accountId
    ? `?accountId=${encodeURIComponent(accountId)}`
    : "";
  const result = await codexMuxRequest(`/profile/combined${query}`);
  globalThis.__codexMuxCombinedProfileAccounts = result.accounts || [];
  return result.profile;
}

// The renderer polls `/wham/usage` over HTTP for the Primary account only, so
// its usage banners, sidebar alert, and reset prompts describe one account
// while the multiplexer routes across the pool. Replace the rate-limit
// windows with the pooled view (mean usage, earliest reset) and clear the
// limit-reached fields while any connected subscription still has weekly
// capacity. A fully depleted pool keeps the native limit-reached response.
async function codexMuxFilterUsageStatus(status) {
  if (status == null || typeof status !== "object") return status;
  let accounts;
  try {
    accounts = (await codexMuxRequest("/accounts")).accounts || [];
    codexMuxRememberAccounts(accounts);
  } catch {
    return status;
  }
  return codexMuxPoolUsageStatus(status, accounts);
}

// Streamed usage snapshots are applied synchronously, so they are pooled with
// the last known accounts instead of waiting for a fresh list.
function codexMuxFilterUsageStatusSync(status) {
  if (status == null || typeof status !== "object") return status;
  const accounts = codexMuxCachedAccounts();
  return accounts.length > 0 ? codexMuxPoolUsageStatus(status, accounts) : status;
}

function codexMuxPoolUsageStatus(status, accounts) {
  const pool = accounts.filter(codexMuxIsUsable);
  if (pool.length < 2) return status;
  const poolHasCapacity = pool.some((account) => {
    const binding = codexMuxBindingWindow(account.rateLimits);
    return binding == null || binding.usedPercent < 100;
  });
  const rateLimit = status.rate_limit;
  const pooledRateLimit =
    rateLimit == null
      ? rateLimit
      : {
          ...rateLimit,
          primary_window: codexMuxPooledUsageWindow(
            rateLimit.primary_window,
            pool.map((account) => account.rateLimits?.primary),
          ),
          secondary_window: codexMuxPooledUsageWindow(
            rateLimit.secondary_window,
            pool.map((account) => account.rateLimits?.secondary),
          ),
        };
  if (!poolHasCapacity) return { ...status, rate_limit: pooledRateLimit };
  return {
    ...status,
    rate_limit_upsell: null,
    rate_limit_reached_type: null,
    rate_limit:
      pooledRateLimit == null
        ? pooledRateLimit
        : { ...pooledRateLimit, allowed: true, limit_reached: false },
  };
}

function codexMuxPooledUsageWindow(window, accountWindows) {
  if (window == null) return window;
  const windows = accountWindows.filter(Boolean);
  if (windows.length === 0) return window;
  const usedPercent =
    windows.reduce((total, entry) => total + entry.usedPercent, 0) /
    windows.length;
  const resets = windows
    .map((entry) => entry.resetsAt)
    .filter((value) => value != null);
  const resetsAt = resets.length === 0 ? null : Math.min(...resets);
  return {
    ...window,
    used_percent: usedPercent,
    reset_at: resetsAt ?? window.reset_at,
  };
}

const codexMuxResetRequests = new Map();

function codexMuxRateLimitResets(accountId) {
  if (codexMuxResetRequests.has(accountId)) {
    return codexMuxResetRequests.get(accountId);
  }
  const request = codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets`,
  ).finally(() => codexMuxResetRequests.delete(accountId));
  codexMuxResetRequests.set(accountId, request);
  return request;
}

function codexMuxResetCountText(count) {
  if (count === undefined) return "Loading resets…";
  if (count === null) return "Couldn’t load resets";
  return count === 1 ? "1 reset available" : `${count} resets available`;
}

function codexMuxAvailableResetCount(resets) {
  if (resets == null || typeof resets !== "object") return null;
  const available = resets.available_count;
  if (available != null) {
    return Number.isSafeInteger(available) && available >= 0 ? available : null;
  }
  const applicable = resets.applicable_available_count;
  if (applicable != null) {
    return Number.isSafeInteger(applicable) && applicable >= 0
      ? applicable
      : null;
  }
  return null;
}

async function codexMuxConsumeRateLimitReset(accountId, input) {
  if (!accountId) throw new Error("Wait for subscription details to load before using a reset.");
  const policy = (await codexMuxRequest("/reset-policy")).resetPolicy;
  const label = codexMuxCachedAccounts().find((account) => account.id === accountId)?.label || "this subscription";
  if (policy !== "auto" && !window.confirm(`Use one reset credit from ${label}? This will spend that reset.`)) {
    throw new Error("Reset cancelled.");
  }
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets/consume`,
    {
      method: "POST",
      body: JSON.stringify({
        confirmed: policy !== "auto",
        creditId: input.creditId ?? null,
        redeemRequestId: input.redeemRequestId,
      }),
    },
  );
}

function CodexMuxUsageModal({
  onClose,
}) {
  return (0, e7.jsx)(QLs, {
    defaultResetCreditsOpen: true,
    initialAvailableCount: 0,
    isRateLimitReached: false,
    onClose,
    onResetComplete: () => {},
  });
}

function CodexMuxUseResetAccountState() {
  const cachedAccounts = codexMuxCachedAccounts().filter(codexMuxIsUsable);
  const [accounts, setAccounts] = kXc.useState(cachedAccounts);
  const [selectedId, setSelectedId] = kXc.useState(
    () =>
      cachedAccounts.find(
        (account) => account.id === globalThis.__codexMuxResetAccountId,
      )?.id ||
      cachedAccounts[0]?.id ||
      null,
  );
  const [resetCounts, setResetCounts] = kXc.useState({});
  const [loading, setLoading] = kXc.useState(cachedAccounts.length === 0);

  const loadAccounts = kXc.useCallback(async () => {
    const connected = (await codexMuxFetchAccounts()).filter(codexMuxIsUsable);
    setAccounts(connected);
    setSelectedId((current) => {
      const next = connected.some((account) => account.id === current)
        ? current
        : connected[0]?.id || null;
      if (next) {
        globalThis.__codexMuxResetAccountId = next;
      } else {
        delete globalThis.__codexMuxResetAccountId;
      }
      return next;
    });
    setLoading(false);
    await Promise.all(
      connected.map(async (account) => {
        let count = null;
        try {
          const resets = await codexMuxRateLimitResets(account.id);
          count = codexMuxAvailableResetCount(resets);
        } catch {}
        setResetCounts((current) => ({ ...current, [account.id]: count }));
      }),
    );
  }, []);

  kXc.useEffect(() => {
    loadAccounts().catch(() => setLoading(false));
  }, [loadAccounts]);

  kXc.useEffect(
    () => () => {
      delete window.__codexMuxResetAccountId;
      delete window.__codexMuxSelectedUsageWindows;
      delete window.__codexMuxResetAccountSelector;
    },
    [],
  );

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;
  const activeId = selected?.id || null;
  if (activeId) {
    window.__codexMuxResetAccountId = activeId;
  } else {
    delete window.__codexMuxResetAccountId;
  }
  window.__codexMuxSelectedUsageWindows = selected
    ? codexMuxUsageWindows(selected.rateLimits)
    : null;
  window.__codexMuxResetAccountSelector = (0, e7.jsx)(
    CodexMuxResetAccountSelector,
    {
      accounts,
      loading,
      resetCounts,
      selectedId: activeId,
      onSelect: (accountId) => {
        window.__codexMuxResetAccountId = accountId;
        setSelectedId(accountId);
      },
      onChanged: () => loadAccounts().catch(() => {}),
    },
  );

}

function CodexMuxResetAccountSelector({
  accounts,
  loading,
  onChanged,
  onSelect,
  resetCounts,
  selectedId,
}) {
  const selected = accounts.find((account) => account.id === selectedId) || null;
  return (0, e7.jsxs)("div", {
    className: "pt-4",
    children: [
      (0, e7.jsx)("div", {
        className:
          "mb-2 px-1 text-xs font-medium text-token-text-secondary",
        children: "Subscription",
      }),
      (0, e7.jsx)("div", {
        className:
          "flex flex-wrap gap-2 rounded-2xl border border-token-border p-2",
        children: loading
          ? (0, e7.jsx)("div", {
              className: "px-2 py-2 text-sm text-token-text-secondary",
              children: "Loading subscriptions…",
            })
          : accounts.map((account) => {
              const selected = account.id === selectedId;
              const count = resetCounts[account.id];
              const credits = codexMuxCreditsText(account.credits);
              const resetText = codexMuxResetCountText(count);
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex min-w-fit items-center gap-2 rounded-xl px-3 py-2 text-left",
                    "transition-colors hover:bg-token-foreground/5",
                    selected
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary",
                  ].join(" "),
                  "aria-pressed": selected,
                  title: codexMuxUsageDetails(account),
                  onClick: () => onSelect(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsxs)("span", {
                      className: "flex min-w-0 flex-col",
                      children: [
                        (0, e7.jsx)("span", {
                          className: "max-w-40 truncate text-sm font-medium",
                          children: account.preferred
                            ? `${codexMuxAccountTitle(account)} · Pinned`
                            : codexMuxAccountTitle(account),
                        }),
                        (0, e7.jsx)("span", {
                          className: "text-xs text-token-text-tertiary",
                          children: credits ? `${resetText} · ${credits}` : resetText,
                        }),
                      ],
                    }),
                  ],
                },
                account.id,
              );
            }),
      }),
      selected && !loading
        ? (0, e7.jsx)(CodexMuxAccountActions, {
            account: selected,
            onChanged,
          })
        : null,
    ],
  });
}

// Per-subscription controls shown under the picker in the Usage sheet.
function CodexMuxAccountActions({ account, onChanged }) {
  const [busy, setBusy] = kXc.useState("");
  const [message, setMessage] = kXc.useState("");
  kXc.useEffect(() => {
    setMessage("");
  }, [account.id]);

  async function run(action, work, done) {
    if (busy) return;
    setBusy(action);
    setMessage("");
    try {
      await work();
      if (done) setMessage(done);
      await onChanged?.();
    } catch (requestError) {
      setMessage(requestError.message);
    } finally {
      setBusy("");
    }
  }

  const button = (action, label, onClick, options = {}) =>
    (0, e7.jsx)(
      "button",
      {
        type: "button",
        disabled: Boolean(busy) || options.disabled,
        title: options.title,
        className: [
          "rounded-lg border border-token-border px-2.5 py-1 text-xs transition-colors",
          "hover:bg-token-foreground/5 disabled:cursor-default disabled:opacity-50",
          options.danger ? "text-token-text-error" : "text-token-text-primary",
        ].join(" "),
        onClick,
        children: busy === action ? "Working…" : label,
      },
      action,
    );

  return (0, e7.jsxs)("div", {
    className: "mt-2 flex flex-col gap-2 px-1",
    children: [
      (0, e7.jsx)("div", {
        className: "text-xs text-token-text-secondary",
        children: codexMuxUsageDetails(account),
      }),
      (0, e7.jsxs)("div", {
        className: "flex flex-wrap gap-2",
        children: [
          account.preferred
            ? button("pin", "Stop pinning new chats", () =>
                run("pin", () => codexMuxSetPreferredAccount(null), "New chats are balanced automatically."),
              )
            : button("pin", "Use for new chats", () =>
                run("pin", () => codexMuxSetPreferredAccount(account.id), `New chats will use ${account.label} while it has usage.`),
              ),
          button("refresh", "Refresh plan & usage", () =>
            run("refresh", () => codexMuxRefreshAccount(account.id), "Refreshed from ChatGPT."),
          ),
          account.controller
            ? null
            : button(
                "pause",
                "Pause",
                () =>
                  run(
                    "pause",
                    () => codexMuxUpdateAccount(account.id, { enabled: false }),
                    `${account.label} is paused. Resume it from the account menu.`,
                  ),
                { title: "Stop routing chats to this subscription" },
              ),
          account.controller
            ? null
            : button(
                "remove",
                "Remove",
                () => {
                  if (!window.confirm(`Remove ${account.label} from the router? Its sign-in is kept in a recoverable folder.`)) return;
                  run("remove", () => codexMuxRemoveAccount(account.id));
                },
                {
                  danger: true,
                  disabled: account.threadCount > 0,
                  title:
                    account.threadCount > 0
                      ? `${account.label} owns ${account.threadCount} chat(s). Pause it instead.`
                      : "Remove this subscription",
                },
              ),
        ],
      }),
      message
        ? (0, e7.jsx)("div", {
            className: "text-xs text-token-text-tertiary",
            children: message,
          })
        : null,
    ],
  });
}

function CodexMuxAccountMenu() {
  const modalScope = Lo(Q);
  const [accounts, setAccounts] = kXc.useState(codexMuxCachedAccounts);
  const [loading, setLoading] = kXc.useState(
    () => !codexMuxCachedAccounts().some((account) => account.connected),
  );
  const [busy, setBusy] = kXc.useState(false);
  const [error, setError] = kXc.useState("");
  const [login, setLogin] = kXc.useState(null);
  const [codeCopied, setCodeCopied] = kXc.useState(false);
  const loginAccountId = login?.accountId || null;

  const refresh = kXc.useCallback(async () => {
    try {
      const nextAccounts = await codexMuxFetchAccounts();
      setAccounts(nextAccounts);
      setError("");
      if (nextAccounts.some((account) => account.connected)) setLoading(false);
    } catch (requestError) {
      setError(requestError.message);
      setLoading(false);
    }
  }, []);

  kXc.useEffect(() => {
    refresh();
    const events = new EventSource(
      `${CODEX_MUX_API}/events?token=${encodeURIComponent(CODEX_MUX_TOKEN)}`,
    );
    events.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (
          payload.type === "account-updated" &&
          payload.accountId === loginAccountId &&
          payload.data?.connected &&
          !payload.data?.needsReauth
        ) {
          setLogin(null);
        }
        if (
          payload.type === "account-updated" ||
          payload.type === "account-removed" ||
          payload.type === "routing-updated"
        ) {
          refresh();
        }
      } catch {}
    };
    const warmupTimer = setTimeout(refresh, 2_000);
    const loadingDeadline = setTimeout(() => {
      refresh().finally(() => setLoading(false));
    }, 6_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      clearTimeout(warmupTimer);
      clearTimeout(loadingDeadline);
      clearInterval(timer);
      events.close();
    };
  }, [refresh, loginAccountId]);

  kXc.useEffect(() => {
    if (!login) return;
    const allowEscapeDismissal = (event) => {
      if (event.key !== "Escape") return;
      setLogin(null);
    };
    window.addEventListener("keydown", allowEscapeDismissal, true);
    return () => window.removeEventListener("keydown", allowEscapeDismissal, true);
  }, [login]);

  const connected = accounts.filter(codexMuxIsUsable);
  const pinned = connected.find((account) => account.preferred) || null;
  const pinnedUnavailable = accounts.find(
    (account) => account.preferred && !codexMuxIsUsable(account),
  );
  const bindingWindows = connected.map((account) =>
    codexMuxBindingWindow(account.rateLimits),
  );
  const hasCompleteUsage =
    connected.length > 0 && bindingWindows.every((binding) => binding != null);
  const totalRemaining = bindingWindows.reduce(
    (total, binding) =>
      total + (binding == null ? 0 : Math.max(0, 100 - binding.usedPercent)),
    0,
  );

  async function perform(event, work) {
    event?.preventDefault?.();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await work();
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function startSignIn(accountId) {
    const result = await codexMuxRequest(`/accounts/${encodeURIComponent(accountId)}/login`, {
      method: "POST",
      body: JSON.stringify({ mode: "chatgptDeviceCode" }),
    });
    setCodeCopied(false);
    setLogin(result.login ? { ...result.login, accountId } : null);
  }

  function addSubscription(event) {
    // The router reuses a slot whose sign-in was never finished, and it picks
    // an unused "Subscription N" label itself.
    return perform(event, async () => {
      const created = await codexMuxRequest("/accounts", {
        method: "POST",
        body: JSON.stringify({ label: "" }),
      });
      await startSignIn(created.account.id);
    });
  }

  function choosePreferred(event, accountId) {
    return perform(event, () => codexMuxSetPreferredAccount(accountId));
  }

  async function copyCodeAndContinue(event) {
    event.preventDefault();
    const userCode = login?.userCode || "";
    const verificationUrl = login?.verificationUrl || login?.authUrl || "";
    const copy = userCode
      ? navigator.clipboard.writeText(userCode)
      : Promise.resolve();
    if (verificationUrl) {
      try {
        const destination = new URL(verificationUrl);
        const trustedHost =
          destination.hostname === "chatgpt.com" ||
          destination.hostname === "auth.openai.com";
        if (destination.protocol !== "https:" || !trustedHost) {
          throw new Error("untrusted verification URL");
        }
        window.open(destination.href, "_blank", "noopener,noreferrer");
      } catch {
        setError("The sign-in verification page could not be opened safely.");
      }
    }
    try {
      await copy;
      setCodeCopied(userCode !== "");
    } catch {
      setError("The sign-in code could not be copied.");
    }
  }

  const avatarIcon = (account) => (iconProps) =>
    (0, e7.jsx)(CodexMuxAccountAvatar, {
      ...iconProps,
      imageUrl: account.profileImageUrl,
      label: account.label,
    });

  const rows = [];
  rows.push(
    (0, e7.jsx)(
      _H,
      {
        LeftIcon: S2,
        SubText: loading
          ? "Connecting subscriptions…"
          : connected.length === 1
            ? "1 connected subscription"
            : `${connected.length} connected subscriptions`,
        rightIcon: (0, e7.jsx)("span", {
          className: "text-token-description-foreground tabular-nums",
          children: loading
            ? "…"
            : hasCompleteUsage
              ? `${Math.round(totalRemaining)}%`
              : "–",
        }),
        onSelect: () => BW(modalScope, CodexMuxUsageModal, {}),
        children: "Usage remaining",
      },
      "codex-mux-total",
    ),
  );

  const resetAuto = globalThis.__codexMuxResetPolicy === "auto";
  for (const [policy, label, detail] of [
    ["ask", "Ask me", "Confirm before spending a reset credit (default)."],
    ["auto", "Use automatically", "Spend a reset only when every subscription is out of usage."],
  ]) {
    rows.push((0, e7.jsx)(_H, {
      SubText: detail,
      rightIcon: (resetAuto ? "auto" : "ask") === policy ? "✓" : null,
      onSelect: (event) => perform(event, () => codexMuxRequest("/reset-policy", {method:"PUT",body:JSON.stringify({resetPolicy:policy})})),
      children: `Usage resets: ${label}`,
    }, `codex-mux-reset-policy-${policy}`));
  }

  if (connected.length > 1) {
    const routingSummary = pinned ? pinned.label : "Automatic";
    const routingDetail = pinned
      ? `Pinned to ${pinned.label} while it has usage`
      : pinnedUnavailable
        ? `${pinnedUnavailable.label} is unavailable, balancing automatically`
        : "Balanced across subscriptions";
    const choices = [
      (0, e7.jsx)(
        _H,
        {
          RightIcon: pinned ? undefined : CodexMuxCheckIcon,
          SubText: "Spread new chats across subscriptions by remaining usage",
          onSelect: (event) => choosePreferred(event, null),
          children: "Automatic",
        },
        "codex-mux-route-auto",
      ),
      ...connected.map((account) =>
        (0, e7.jsx)(
          _H,
          {
            LeftIcon: avatarIcon(account),
            RightIcon: account.preferred ? CodexMuxCheckIcon : undefined,
            SubText: codexMuxUsageDetails(account),
            subTextAllowWrap: true,
            onSelect: (event) => choosePreferred(event, account.id),
            children: codexMuxAccountTitle(account),
          },
          `codex-mux-route-${account.id}`,
        ),
      ),
    ];
    rows.push((0, e7.jsx)(CH.Separator, {}, "codex-mux-routing-separator"));
    if (CH.FlyoutSubmenuItem) {
      rows.push(
        (0, e7.jsx)(
          CH.FlyoutSubmenuItem,
          {
            LeftIcon: CodexMuxRouteIcon,
            label: "New chats use",
            tooltipText: routingDetail,
            rightIcon: (0, e7.jsx)("span", {
              className: "ms-2 max-w-28 truncate text-token-description-foreground",
              children: routingSummary,
            }),
            children: choices,
          },
          "codex-mux-routing",
        ),
      );
    } else {
      rows.push(
        (0, e7.jsx)(
          _H,
          {
            LeftIcon: CodexMuxRouteIcon,
            SubText: pinned ? `${routingDetail} · Click for automatic` : routingDetail,
            onSelect: (event) => choosePreferred(event, null),
            children: `New chats use: ${routingSummary}`,
          },
          "codex-mux-routing",
        ),
      );
    }
  }

  if (connected.length > 0) {
    rows.push(
      (0, e7.jsx)(CH.Separator, {}, "codex-mux-accounts-separator"),
    );
  }

  for (const account of connected) {
    const binding = codexMuxBindingWindow(account.rateLimits);
    const remaining =
      binding == null ? null : Math.max(0, 100 - binding.usedPercent);
    const credits = codexMuxCreditsText(account.credits);
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: avatarIcon(account),
          SubText: (0, e7.jsxs)(e7.Fragment, {
            children: [
              account.email
                ? (0, e7.jsx)(CodexMuxMaskedEmail, { email: account.email })
                : account.planLabel || "ChatGPT subscription",
              credits ? ` · ${credits}` : null,
            ],
          }),
          className: "group",
          tooltipText: `${codexMuxUsageDetails(account)}. ${
            account.preferred
              ? "New chats use this subscription. Click to go back to automatic."
              : "Click to use this subscription for new chats."
          }`,
          onSelect: (event) =>
            choosePreferred(event, account.preferred ? null : account.id),
          rightIcon: (0, e7.jsxs)("span", {
            className: "flex items-center gap-1 text-token-description-foreground tabular-nums",
            children: [
              account.preferred
                ? (0, e7.jsx)(CodexMuxPinIcon, { className: "icon-xs" })
                : null,
              remaining == null ? "–" : `${Math.round(remaining)}%`,
            ],
          }),
          children: codexMuxAccountTitle(account),
        },
        `codex-mux-account-${account.id}`,
      ),
    );
  }

  // Accounts that exist but cannot take chats, each with the one action that
  // fixes it.
  for (const account of accounts) {
    if (codexMuxIsUsable(account)) continue;
    let status;
    let detail;
    let tone;
    let action;
    if (account.connected && account.needsReauth) {
      status = "Sign-in expired";
      detail = "ChatGPT rejected this sign-in (common after a plan change). Click to sign in again.";
      tone = "danger";
      action = (event) => perform(event, () => startSignIn(account.id));
    } else if (!account.enabled) {
      status = "Paused";
      detail = "Not used for chats. Click to resume.";
      action = (event) =>
        perform(event, () => codexMuxUpdateAccount(account.id, { enabled: true }));
    } else if (!account.connected && typeof account.error === "string" && account.error.trim() !== "") {
      status = "Reconnecting";
      detail = "Couldn’t refresh this subscription. Retrying automatically.";
      tone = "danger";
    } else if (!account.connected && !account.controller) {
      status = "Not signed in";
      detail = "Click to finish signing in.";
      action = (event) => perform(event, () => startSignIn(account.id));
    } else {
      continue;
    }
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: avatarIcon(account),
          SubText: detail,
          tone,
          allowWrap: true,
          subTextAllowWrap: true,
          onSelect: action,
          children: `${account.label} · ${status}`,
        },
        `codex-mux-unavailable-${account.id}`,
      ),
    );
  }

  if (login) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxCopyIcon,
          SubText: login.userCode
            ? codeCopied
              ? `Code ${login.userCode} copied`
              : `Code ${login.userCode} · Click to copy`
            : "Finish signing in with ChatGPT",
          onSelect: copyCodeAndContinue,
          children: "Continue sign-in",
        },
        "codex-mux-login",
      ),
    );
  }

  if (error) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: S2,
          SubText: error,
          tone: "danger",
          allowWrap: true,
          subTextAllowWrap: true,
          children: "Subscription pool unavailable",
        },
        "codex-mux-error",
      ),
    );
  }

  if (!loading) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxPlusIcon,
          onSelect: addSubscription,
          children: busy ? "Working…" : "Add another subscription",
        },
        "codex-mux-add",
      ),
    );
  }
  rows.push((0, e7.jsx)(CH.Separator, {}, "codex-mux-separator"));
  return (0, e7.jsx)(e7.Fragment, { children: rows });
}

function CodexMuxUsageIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M3.75 13.25a6.25 6.25 0 1 1 12.5 0M10 13.25l3-4.5",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function CodexMuxCheckIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M4.75 10.5l3.5 3.5 7-8",
      stroke: "currentColor",
      strokeWidth: 1.6,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function CodexMuxPinIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-label": "Used for new chats",
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M7.75 3.75h4.5l-.75 4.5 2.75 2.5v1h-8.5v-1l2.75-2.5-.75-4.5zM10 11.75v4.5",
      stroke: "currentColor",
      strokeWidth: 1.4,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function CodexMuxRouteIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M4.25 5.75h7.5a3 3 0 010 6h-3.5a2.5 2.5 0 000 5h7.5M13.25 14.25l2.5 2.5-2.5 2.5M6.25 3.25l-2.5 2.5 2.5 2.5",
      stroke: "currentColor",
      strokeWidth: 1.4,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function codexMuxBindingWindow(rateLimits) {
  // The window that actually stops a request, which is whichever is most
  // spent - not the longest one. ChatGPT enforces a short window alongside
  // the weekly one, so an account can sit at 68% weekly and still refuse
  // every request because its five-hour window is gone.
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  if (windows.length === 0) return null;
  return windows.reduce((worst, entry) =>
    entry.usedPercent > worst.usedPercent ? entry : worst,
  );
}

function codexMuxUsageWindows(rateLimits) {
  return [rateLimits?.primary, rateLimits?.secondary]
    .filter(Boolean)
    .map((window) => ({
      usedPercent: window.usedPercent,
      remainingPercent: Math.max(0, 100 - window.usedPercent),
      windowMinutes: window.windowDurationMins || 0,
      resetsAt: window.resetsAt ?? null,
    }));
}

function CodexMuxPlusIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M10 4.25v11.5M4.25 10h11.5",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
    }),
  });
}

function CodexMuxCopyIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsxs)(e7.Fragment, {
      children: [
        (0, e7.jsx)("rect", {
          x: 6.25,
          y: 6.25,
          width: 9.5,
          height: 9.5,
          rx: 2,
          stroke: "currentColor",
          strokeWidth: 1.5,
        }),
        (0, e7.jsx)("path", {
          d: "M13.75 6.25V6A1.75 1.75 0 0 0 12 4.25H6A1.75 1.75 0 0 0 4.25 6v6c0 .97.78 1.75 1.75 1.75h.25",
          stroke: "currentColor",
          strokeWidth: 1.5,
          strokeLinecap: "round",
        }),
      ],
    }),
  });
}

function CodexMuxMaskedEmail({ email }) {
  return (0, e7.jsxs)(e7.Fragment, {
    children: [
      (0, e7.jsx)("span", {
        className: "group-hover:hidden",
        children: "••••••••",
      }),
      (0, e7.jsx)("span", {
        className: "hidden group-hover:inline",
        children: email,
      }),
    ],
  });
}

function CodexMuxAccountAvatar({ imageUrl, label, className }) {
  const [failed, setFailed] = kXc.useState(false);
  const resolvedImageUrl = jLa(imageUrl || null);
  if (resolvedImageUrl && !failed) {
    return (0, e7.jsx)("img", {
      src: resolvedImageUrl,
      alt: "",
      className: `${className || "icon-sm"} rounded-full object-cover`,
      referrerPolicy: "no-referrer",
      onError: () => setFailed(true),
    });
  }
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (0, e7.jsx)("span", {
    className: `${className || "icon-sm"} flex items-center justify-center rounded-full bg-token-charts-purple/10 text-[9px] leading-none text-token-charts-purple`,
    "aria-hidden": true,
    children: initials || "?",
  });
}

function CodexMuxOverlappingAvatars({ accounts, size = "size-20" }) {
  const overlapClass = size === "size-20" ? "-ml-10" : "-ml-2";
  return (0, e7.jsx)("div", {
    className: "flex items-center justify-center",
    children: accounts.map((account, index) =>
      (0, e7.jsx)(
        "span",
        {
          className: `${index === 0 ? "" : overlapClass} rounded-full border-4 border-token-bg-primary`,
          title: account.planLabel
            ? `${account.label} · ${account.planLabel}`
            : account.label,
          children: (0, e7.jsx)(CodexMuxAccountAvatar, {
            imageUrl: account.profileImageUrl,
            label: account.label,
            className: size,
          }),
        },
        account.id,
      ),
    ),
  });
}

function CodexMuxProfileAvatarStack({ onSelect }) {
  const [accounts, setAccounts] = kXc.useState(
    globalThis.__codexMuxCombinedProfileAccounts || [],
  );
  const [selectedId, setSelectedId] = kXc.useState(
    globalThis.__codexMuxSelectedProfileAccountId || null,
  );
  kXc.useEffect(() => {
    let live = true;
    codexMuxRequest("/accounts")
      .then((result) => {
        if (!live) return;
        const connected = (result.accounts || []).filter(codexMuxIsUsable);
        globalThis.__codexMuxCombinedProfileAccounts = connected;
        setAccounts(connected);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  kXc.useEffect(() => {
    globalThis.__codexMuxSelectedProfileAccountId = null;
    setSelectedId(null);
    onSelect?.();
    return () => {
      globalThis.__codexMuxSelectedProfileAccountId = null;
    };
  }, []);
  if (accounts.length === 0) return null;
  const visibleAccounts = selectedId
    ? accounts.filter((account) => account.id === selectedId)
    : accounts;
  return (0, e7.jsx)("div", {
    className: "mb-4",
    "aria-label": selectedId
      ? "Selected subscription profile"
      : `${accounts.length} connected subscriptions`,
    children: (0, e7.jsx)("div", {
      className: "flex items-center justify-center",
      children: visibleAccounts.map((account, index) =>
        (0, e7.jsx)(
          "button",
          {
            type: "button",
            className: `${index === 0 ? "" : "-ml-5"} rounded-full border-4 border-token-bg-primary transition-transform hover:z-10 hover:scale-105 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-token-focus-border`,
            style: {
              marginLeft: index === 0 ? 0 : -20,
              zIndex: index,
            },
            "aria-label": selectedId
              ? `Show combined profile stats`
              : `Show ${account.label} profile stats`,
            title: account.planLabel
              ? `${account.label} · ${account.planLabel}`
              : account.label,
            onClick: () => {
              const nextId = selectedId === account.id ? null : account.id;
              globalThis.__codexMuxSelectedProfileAccountId = nextId;
              setSelectedId(nextId);
              onSelect?.();
            },
            children: (0, e7.jsx)(CodexMuxAccountAvatar, {
              imageUrl: account.profileImageUrl,
              label: account.label,
              className: "size-20",
            }),
          },
          account.id,
        ),
      ),
    }),
  });
}

function CodexMuxPluginScope() {
  const cachedAccounts = codexMuxCachedAccounts().filter(
    (account) => account.connected && account.enabled,
  );
  const [accounts, setAccounts] = kXc.useState(cachedAccounts);
  const [selectedId, setSelectedId] = kXc.useState("primary");
  const [loading, setLoading] = kXc.useState(cachedAccounts.length === 0);
  const queryClient = lt();
  kXc.useEffect(() => {
    let live = true;
    codexMuxFetchAccounts()
      .then((connectedAccounts) => {
        if (!live) return;
        setAccounts(
          connectedAccounts.filter(
            (account) => account.connected && account.enabled,
          ),
        );
      })
      .catch(() => {})
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  kXc.useEffect(() => {
    globalThis.__codexMuxPluginAccountId = selectedId;
    return () => {
      delete globalThis.__codexMuxPluginAccountId;
    };
  }, [selectedId]);

  async function selectAccount(accountId) {
    if (accountId === selectedId) return;
    globalThis.__codexMuxPluginAccountId = accountId;
    setSelectedId(accountId);
    await queryClient.invalidateQueries({
      predicate: (query) => {
        const root = query.queryKey?.[0];
        return root === "apps" || root === "plugins" || root === "mcp";
      },
    });
  }

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;

  return (0, e7.jsxs)("div", {
    className:
      "mb-5 rounded-2xl border border-token-border-light p-3",
    children: [
      (0, e7.jsxs)("div", {
        className: "px-1",
        children: [
          (0, e7.jsx)("div", {
            className: "text-sm font-medium text-token-text-primary",
            children: "Plugin connections",
          }),
          (0, e7.jsx)("div", {
            className: "mt-0.5 text-xs text-token-text-secondary",
            children: selected
              ? `Installs are shared. Connection access below is for ${selected.label}.`
              : "Installs are shared. Choose a subscription for connection access.",
          }),
        ],
      }),
      loading
        ? (0, e7.jsx)("div", {
            className: "mt-3 px-1 text-sm text-token-text-tertiary",
            children: "Loading subscriptions…",
          })
        : (0, e7.jsx)("div", {
            className: "mt-3 flex flex-wrap gap-2",
            children: accounts.map((account) => {
              const active = account.id === selected?.id;
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex items-center gap-2 rounded-xl px-2.5 py-2 text-sm transition-colors",
                    active
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary hover:bg-token-foreground/5",
                  ].join(" "),
                  "aria-pressed": active,
                  onClick: () => selectAccount(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsx)("span", {
                      children: account.planLabel
                        ? `${account.label} · ${account.planLabel}`
                        : account.label,
                    }),
                  ],
                },
                account.id,
              );
            }),
          }),
    ],
  });
}

// The thread summary is emitted into a separate lazy-loaded renderer chunk.
// Export the same avatar component so both surfaces share image resolution,
// error handling, and the initials fallback.
globalThis.CodexMuxAccountAvatar = CodexMuxAccountAvatar;
globalThis.codexMuxScopePluginRequest = codexMuxScopePluginRequest;
globalThis.codexMuxFilterUsageStatus = codexMuxFilterUsageStatus;
globalThis.codexMuxFilterUsageStatusSync = codexMuxFilterUsageStatusSync;
globalThis.CodexMuxUseResetAccountState = CodexMuxUseResetAccountState;
globalThis.codexMuxProfileData = codexMuxProfileData;
globalThis.codexMuxRateLimitResets = codexMuxRateLimitResets;
globalThis.codexMuxConsumeRateLimitReset = codexMuxConsumeRateLimitReset;
globalThis.codexMuxResetCountText = codexMuxResetCountText;
globalThis.codexMuxAvailableResetCount = codexMuxAvailableResetCount;
globalThis.codexMuxBindingWindow = codexMuxBindingWindow;
globalThis.codexMuxIsUsable = codexMuxIsUsable;
globalThis.codexMuxCreditsText = codexMuxCreditsText;
globalThis.codexMuxUsageDetails = codexMuxUsageDetails;
globalThis.CodexMuxProfileAvatarStack = (props) =>
  (0, e7.jsx)(CodexMuxProfileAvatarStack, props || {});
globalThis.CodexMuxPluginScope = () =>
  (0, e7.jsx)(CodexMuxPluginScope, {});
