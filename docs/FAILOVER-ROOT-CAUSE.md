# Why mid-conversation failover never fires

Failover is implemented and correct, and it never runs. This records why, with
the evidence, so the fix is not guesswork.

## Symptom

A chat pinned to an exhausted account stops with "You've hit your usage limit"
while another connected account sits at 0% used. Observed three times in one
day on build 8109. The user must manually start a new chat, which is the only
point at which an account is chosen.

## The failover path that exists

`handleInbound` triggers failover here:

    if route.method == "turn/start" && isUsageLimitResponse(message) {
        go m.retryTurnAfterUsageLimit(route, inbound.AccountID)

`retryTurnAfterUsageLimit` -> `failoverTurn` then picks an unexhausted account,
resumes the thread on it, reassigns the owner and re-forwards the turn. That
code is fine.

The branch it sits in only runs for a **response**: `message.Method == ""` with
a request id, matched against a recorded external route. So failover requires
the usage limit to arrive as a JSON-RPC error response to `turn/start`.

## What actually happens

From an account's own engine log at the moment a run stalled
(`~/.codex-mux/accounts/<id>/codex-home/logs_2.sqlite`, table `logs`, column
`feedback_log_body`):

    18244  codex_core::session::turn
           run_turn: Turn error: You've hit your usage limit. ...
           or try again at 5:54 AM.
    18249  app-server event: thread/status/changed  targeted_connections=0
    18250  app-server event: error                  targeted_connections=1
    18253  app-server event: turn/completed         targeted_connections=1

`turn/start` is accepted and answered successfully. The turn then fails later
inside the engine's session loop, and the failure reaches the router as a
notification with method `error`, followed by `turn/completed`.

A notification has a method and no id, so it never reaches the response branch,
the recorded `turn/start` route is already consumed, and the failover trigger is
structurally unreachable for this shape.

## What a fix needs

1. Detect the usage limit on the `error` notification, not only on a response.
   The existing `isUsageLimitResponse` text match over message and data is the
   right shape of check; the payload schema of the `error` event is not
   recorded in the engine log and should be captured from a live occurrence
   before parsing anything structurally.
2. Know which turn to retry. The `error` event is not known to carry a thread
   id. Track the in-flight `turn/start` per account when it is forwarded, and
   key the retry off `inbound.AccountID`, which is always known.
3. Suppress the forwarded `error` and the following `turn/completed` for a turn
   that is being failed over, or the client renders the usage-limit message and
   ends the turn even though the retry succeeds.
4. Keep the existing response-path trigger. Both shapes should fail over.

## Capturing the payload

`CODEX_MUX_TRACE_FILE` appends the raw JSON of inbound messages whose method is
listed in `CODEX_MUX_TRACE_METHODS` (comma separated, default `error`). Tracing
is off unless the file is set, and it records only the methods asked for
because payloads can quote model or user text.

    CODEX_MUX_TRACE_FILE=/tmp/codex-mux-trace.jsonl open -a "Codex Subscription Router"

Then send a turn on an account that is already exhausted. The stall itself is
the capture: the engine emits the `error` notification immediately, and the
request costs nothing because the account is already refusing work.

## Not yet done

The `error` payload has not been captured yet, so no parsing is written against
it. Capture one, then implement the four points above.
