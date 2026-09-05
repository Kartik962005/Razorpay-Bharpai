# Architecture

One agent core, two worlds. The planner, policy engine, executor and audit log are identical
whether they are driving a simulation of five hundred cases or a real Razorpay test account —
only the gateway underneath changes. That is what makes the measured results and the live demo
statements about the same system rather than two separate demos.

```
                    ┌──────────────────────────────────────────────┐
                    │                 AGENT CORE                   │
   ┌────────────┐   │                                              │
   │  DETECT    │──►│  taxonomy.py     error_reason × source ×     │
   │            │   │                  step  →  12 root causes     │
   │ webhook or │   │        │                                     │
   │ poller     │   │        ▼                                     │
   └────────────┘   │  planner.py      scores (action, time) pairs │
                    │        │         over a 3-day horizon        │
                    │        │                                     │
                    │        ▼                                     │
                    │  policy.py       R01–R41: allow / deny,      │
                    │        │         and *when* instead          │◄── policy.yaml
                    │        │              ▲                      │
                    │        │              │ veto                 │
                    │        │         ┌────┴─────┐                │
                    │        │         │   llm.py │ advises only   │
                    │        ▼         └──────────┘                │
                    │  executor.py     refetch → act → record      │
                    │        │                                     │
                    │        ▼                                     │
                    │  audit.py        append-only, rule ids on    │
                    │                  every line                  │
                    └────────┬─────────────────────────────────────┘
                             │
              ┌──────────────┴───────────────┐
              ▼                              ▼
     ┌──────────────────┐          ┌─────────────────────┐
     │  razorpay_fake   │          │   razorpay_live     │
     │  driven by the   │          │   real test-mode    │
     │  hidden customer │          │   API calls         │
     │  model           │          │                     │
     └────────┬─────────┘          └──────────┬──────────┘
              │                               │
         sim/world.py                    live/poller.py
         sim/customer.py                 live/webhook.py
         sim/generator.py                live/seed.py
         sim/runner.py                   api/app.py
              │                               │
              ▼                               ▼
        results/report.md              dashboard on :8000
```

## The seam that matters

`adapters/gateway.py` is four methods: `refresh`, `create_payment_link`, `notify`, `retry_charge`.
Everything above it is ignorant of which implementation it has. Two consequences:

* The batch measures the same decision logic the live demo executes.
* Where test mode cannot do something — a server-side retry — the live adapter says so, and the
  agent treats it like any other unavailable action rather than the code special-casing it.

## Why the planner scores times, not just actions

The single most valuable thing a recovery system knows is *when* to act. A balance failure retried
in the first hour converts at roughly a tenth of the same retry on payday. So the planner does not
ask "what is the best action" — it asks "what is the best (action, moment) pair in the next three
days", snapping each candidate forward to the first moment the policy engine would permit it, and
discounting for delay. `WAIT` is a first-class decision with a reason and a target time.

This is also how a purely regulatory step becomes a revenue step. A pre-debit notification recovers
nothing by itself, so it is priced at the value of the retry it unlocks 24 hours later. Without it
every mandate in the batch was permanently unchargeable under R13; with it, subscriptions recover.

## Why the policy engine holds the veto

The language model is useful for three things — writing Hinglish that sounds like a person,
reading replies that patterns mishandle, and briefing a human. None of those require it to decide
what happens to someone's money.

So the engine computes the legal action set first and hands the model that list. The model may
reorder it and choose channel, tone and language. Every override is re-checked before execution;
two rejected proposals on one case escalate it to a human under R34. The model can make a decision
better, never wider.

The same principle applies one level down, to reading replies: `core/replies.py` matches opt-out
and dispute by pattern regardless of what the model concluded, and the pattern wins. A hard stop
that depends on a model getting it right is not a hard stop.

## Why the simulator is adversarial to the agent

The batch exists to attack the design, not to flatter it, so three things are kept apart:

* **Beliefs vs truth.** The planner's priors (`core/taxonomy.py`) and the simulation's behaviour
  model (`sim/config.yaml`) are different shapes, drawn from different reasoning. If they agreed,
  the batch would only prove the agent can read its own notes.
* **Hidden state stays hidden.** Liquidity, intent, patience and whether someone would have paid
  anyway live in `sim/customer.py` and are never reachable from the `Case` object the planner sees.
  A test asserts this.
* **Every policy is judged by the same engine.** The executor asks the policy engine for a verdict
  on every action *including the baselines that never consult it*, and writes it to the audit log.
  That is why the naive baseline's 2,256 violations are a measurement rather than an accusation.

Common random numbers mean two policies reaching the same decision point on the same case draw the
same luck, so a comparison between them measures judgement rather than noise.

## Data flow of one case

```
payment.failed ──► Case(error=ErrorTriple(reason, source, step))
                        │
              diagnose ─┤ RootCause + tags (e.g. afa_required)
                        │
                   plan ┤ candidate actions → legal times → expected values
                        │
                 verdict┤ Denial(action, rule_id, reason, earliest_at)
                        │
                execute ┤ refresh → gateway call → Message / Ticket
                        │
                 record ┤ AuditEntry(kind, actor, rule_ids, summary, payload)
                        │
                 verify ┤ refetch status
                        ▼
                    Outcome
```

`Denial.earliest_at` is load-bearing and was the source of the subtlest bug in the build: a rule
that blocks an action *temporarily* must say when it lifts, or the planner cannot distinguish it
from a permanent bar and abandons recoverable cases. See `docs/BUILD_LOG.md`.

## Persistence

The simulation keeps everything in memory and writes JSONL audit logs per policy. Live mode writes
`.live/cases.json`, `.live/messages.jsonl`, `.live/cursor.json` and `.live/audit.jsonl` — files a
person can open and read, which is worth more than a schema for something whose purpose is to be
inspected.

Two decisions here were bought with bugs, and both are worth stating.

**What persists is a policy question, not a storage question.** The per-customer weekly messaging
cap is derived from what has been sent. Holding that in memory meant a restart silently re-opened
the budget and the same person could be messaged again — a rule broken by an implementation detail
rather than by a decision. Anything a rule is computed from has to outlive the process.

**Paths resolve from one directory at call time.** They used to be module constants computed at
import, so a test could isolate some files and miss others — and adding a new file later escaped
the isolation entirely, which happened twice. Redirecting `STATE_DIR` now redirects everything,
including files that do not exist yet. A defence that must be updated whenever the code grows is
not a defence.

## Testing

194 tests, no network required.

| area | what is pinned |
|---|---|
| `test_taxonomy` | every documented reason resolves; unmapped reasons fall back by source; AFA tagging |
| `test_policy_windows` | TRAI, RBI and NPCI windows, and the *time* each denial defers to |
| `test_hard_stops` | the seven conditions that end a case, and the two actions exempt from them |
| `test_caps` · `test_economics` · `test_afa` | frequency, cost and the ₹15,000 threshold |
| `test_planner` | max-EV selection, waiting for payday, refusing to text at night, rolling caps |
| `test_executor` | idempotency, guardrail fallback, rule-breaks recorded for any policy |
| `test_llm_fallback` | no key, dead model, rogue model — vetoes, and hard stops the model cannot override |
| `test_sim` | reproducibility, mix, hidden-state isolation, zero violations for the agent |
| `test_webhook` | signature, tampering, deduplication, both URL paths |
| `test_live` | real payment shapes, SDK spelling quirks, pagination, honest reporting of what test mode cannot do |
| `test_baselines` | the platform baseline is fair, and the agent still wins under hostile assumptions |
| `test_cli` | every command runs — added after a rename left a dead reference the unit tests could not see |
