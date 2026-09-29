const CODEX_MUX_THREAD_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_THREAD_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

function CodexMuxThreadSubscription() {
  const route = $n(sr);
  const threadId =
    route.value.routeKind === "local-thread" ? route.value.conversationId : null;
  const [account, setAccount] = TE.useState(null);
  const [choices, setChoices] = TE.useState([]);
  const [moving, setMoving] = TE.useState(false);
  const [moveError, setMoveError] = TE.useState("");

  TE.useEffect(() => {
    let active = true;
    if (!threadId) {
      setAccount(null);
      return () => {
        active = false;
      };
    }

    const refresh = async () => {
      try {
        const response = await fetch(
          `${CODEX_MUX_THREAD_API}/thread-account?threadId=${encodeURIComponent(threadId)}`,
          { headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN } },
        );
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const body = await response.json();
        if (active) setAccount(body.account || null);
      } catch {
        if (active) setAccount(null);
      }
      try {
        const response = await fetch(`${CODEX_MUX_THREAD_API}/accounts`, {
          headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN },
        });
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const body = await response.json();
        if (active) {
          setChoices(
            (body.accounts || []).filter(
              (entry) =>
                entry.enabled &&
                entry.connected &&
                !entry.needsReauth &&
                (!entry.authType || entry.authType === "chatgpt"),
            ),
          );
        }
      } catch {
        if (active) setChoices([]);
      }
    };

    refresh();
    const events = new EventSource(
      `${CODEX_MUX_THREAD_API}/events?token=${encodeURIComponent(CODEX_MUX_THREAD_TOKEN)}`,
    );
    events.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (
          payload.type === "account-updated" ||
          payload.type === "account-removed" ||
          ((payload.type === "thread-failed-over" ||
            payload.type === "thread-moved") &&
            payload.data?.threadId === threadId)
        ) {
          refresh();
        }
      } catch {}
    };
    const warmupTimer = setTimeout(refresh, 2_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      active = false;
      clearTimeout(warmupTimer);
      clearInterval(timer);
      events.close();
    };
  }, [threadId]);

  TE.useEffect(() => {
    setMoveError("");
  }, [threadId]);

  async function moveThread(accountId) {
    if (!threadId || !accountId || accountId === account?.id || moving) return;
    setMoving(true);
    setMoveError("");
    try {
      const response = await fetch(`${CODEX_MUX_THREAD_API}/thread-account`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN,
        },
        body: JSON.stringify({ threadId, accountId }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
      setAccount(body.account || null);
    } catch (requestError) {
      setMoveError(requestError.message);
    } finally {
      setMoving(false);
    }
  }

  if (!account) return null;
  const weekly = codexMuxThreadWeeklyWindow(account.rateLimits);
  const remaining = weekly == null ? null : Math.max(0, 100 - weekly.usedPercent);
  const depleted = remaining === 0;
  const AccountAvatar = globalThis.CodexMuxAccountAvatar;
  const otherChoices = choices.filter((entry) => entry.id !== account.id);
  return (0, zE.jsx)(K.Section, {
    sectionKey: "codex-mux-subscription",
    title: "Subscription",
    children: (0, zE.jsxs)("div", {
      className: "flex flex-col",
      children: [
        codexMuxThreadAccountRow(account, AccountAvatar, remaining, depleted),
        otherChoices.length > 0
          ? (0, zE.jsxs)("label", {
              className: "flex items-center justify-between gap-3 pb-1 text-xs text-token-text-secondary",
              children: [
                (0, zE.jsx)("span", {
                  children: moving ? "Moving chat…" : "Continue this chat on",
                }),
                (0, zE.jsxs)("select", {
                  className:
                    "max-w-48 truncate rounded-md border border-token-border bg-transparent px-1.5 py-0.5 text-xs text-token-text-primary",
                  value: "",
                  disabled: moving,
                  "aria-label": "Move this chat to another subscription",
                  onChange: (event) => moveThread(event.target.value),
                  children: [
                    (0, zE.jsx)("option", { value: "", children: "Choose subscription…" }, ""),
                    ...otherChoices.map((entry) => {
                      const limit = codexMuxThreadWeeklyWindow(entry.rateLimits);
                      const left = limit == null ? null : Math.max(0, Math.round(100 - limit.usedPercent));
                      const title = entry.planLabel ? `${entry.label} · ${entry.planLabel}` : entry.label;
                      return (0, zE.jsx)(
                        "option",
                        { value: entry.id, children: left == null ? title : `${title} (${left}% left)` },
                        entry.id,
                      );
                    }),
                  ],
                }),
              ],
            })
          : null,
        moveError
          ? (0, zE.jsx)("div", {
              className: "pb-1 text-xs text-token-text-error",
              children: moveError,
            })
          : null,
      ],
    }),
  });
}

function codexMuxThreadAccountRow(account, AccountAvatar, remaining, depleted) {
  return (0, zE.jsxs)("div", {
      className: "flex min-h-9 items-center justify-between gap-3 py-1 text-sm",
      children: [
        (0, zE.jsxs)("div", {
          className: "flex min-w-0 items-center gap-2",
          children: [
            AccountAvatar
              ? (0, zE.jsx)(AccountAvatar, {
                  imageUrl: account.profileImageUrl,
                  label: account.label,
                  className: "size-5 shrink-0",
                })
              : null,
            (0, zE.jsx)("span", {
              className: "truncate text-token-text-primary",
              children: account.planLabel
                ? `${account.label} · ${account.planLabel}`
                : account.label,
              title: "Follow-up messages in this chat use this subscription",
            }),
          ],
        }),
        (0, zE.jsx)("span", {
          className: "shrink-0 tabular-nums text-token-description-foreground",
          children: account.needsReauth
            ? "Sign-in expired"
            : remaining == null
              ? "Usage unavailable"
              : depleted
                ? "Depleted"
                : `${Math.round(remaining)}% remaining`,
        }),
      ],
  });
}

function codexMuxThreadWeeklyWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}
