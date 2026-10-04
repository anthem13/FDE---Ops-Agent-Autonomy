# Operations agent: safe autonomy boundary

Planner: **heuristic** · emails: **400** · epsilon: **1.0%** errors per email

`safe_scope = max(permission) s.t. error_rate < epsilon`

## Answer

Highest level within budget: **L7 full** (observed error rate 0.00%, 95% upper bound 0.67%).

Levels above L5 +refund<=$50 add no automation: the guards still route those actions (refunds > $200, cancellations) to a human. Least privilege says **grant L5 +refund<=$50**.

**Recommended scope: L5 +refund<=$50.** At that level the agent completes 62.7% of emails end-to-end, with 149 human interventions, 0 duplicate and 0 unauthorized actions, saving **34.6 hours** net of cleanup.

Without guards (idempotency keys, identity/injection/grounding checks, plan-level holds) the safe scope is **L0 shadow**.

### How the boundary moved

Policy v1 only checked that a refund matched *some* charge on the account. Climbing the ladder stopped at **L4 +plan**: at L5 +refund<=$50 it produced 36 unauthorized changes ("refund the last two charges" emails were refunded only the first charge: a real charge, but the wrong amount). Policy v2 adds one guard: the refund must equal the total the customer requested. That moves the safe scope to **L7 full**.

![autonomy](autonomy_curve.svg)

![hours](hours_saved.svg)

## Shadow mode: proposal precision per capability

| capability | correct / proposed | precision |
|---|---|---|
| advance_ticket | 263/368 | 71.5% |
| cancel_account | 0/21 | 0.0% |
| refund:any | 0/14 | 0.0% |
| refund<=50 | 64/116 | 55.2% |
| refund<=500 | 0/35 | 0.0% |
| send_reply | 263/368 | 71.5% |
| update_record:address | 57/84 | 67.9% |
| update_record:plan | 24/46 | 52.2% |

## Ladder with guards ON (policy v2)

| level | duplicates | unauthorized | human interventions | completion | error rate | hours saved | timeouts | dedup hits |
|---|---|---|---|---|---|---|---|---|
| L0 shadow | 0 | 0 | 400 | 0.0% | 0.00% | 0.0 | 72 | 72 |
| L1 replies | 0 | 0 | 400 | 0.0% | 0.00% | 20.0 | 72 | 72 |
| L2 +tickets | 0 | 0 | 326 | 18.5% | 0.00% | 24.3 | 72 | 72 |
| L3 +address | 0 | 0 | 256 | 36.0% | 0.00% | 28.4 | 72 | 111 |
| L4 +plan | 0 | 0 | 228 | 43.0% | 0.00% | 30.0 | 72 | 123 |
| L5 +refund<=$50 | 0 | 0 | 149 | 62.7% | 0.00% | 34.6 | 72 | 168 |
| L6 +refund<=$500 | 0 | 0 | 149 | 62.7% | 0.00% | 34.6 | 72 | 168 |
| L7 full | 0 | 0 | 149 | 62.7% | 0.00% | 34.6 | 72 | 168 |

## Ladder with guards ON (policy v1)

| level | duplicates | unauthorized | human interventions | completion | error rate | hours saved | timeouts | dedup hits |
|---|---|---|---|---|---|---|---|---|
| L0 shadow | 0 | 0 | 400 | 0.0% | 0.00% | 0.0 | 72 | 72 |
| L1 replies | 0 | 0 | 400 | 0.0% | 0.00% | 20.0 | 72 | 72 |
| L2 +tickets | 0 | 0 | 326 | 18.5% | 0.00% | 24.3 | 72 | 72 |
| L3 +address | 0 | 0 | 256 | 36.0% | 0.00% | 28.4 | 72 | 111 |
| L4 +plan | 0 | 0 | 228 | 43.0% | 0.00% | 30.0 | 72 | 123 |
| L5 +refund<=$50 | 0 | 36 | 113 | 62.7% | 9.00% | 21.7 | 72 | 168 |
| L6 +refund<=$500 | 0 | 44 | 105 | 62.7% | 11.00% | 18.9 | 72 | 168 |
| L7 full | 0 | 44 | 105 | 62.7% | 11.00% | 18.9 | 72 | 168 |

## Ladder with guards OFF

| level | duplicates | unauthorized | human interventions | completion | error rate | hours saved | timeouts | dedup hits |
|---|---|---|---|---|---|---|---|---|
| L0 shadow | 0 | 0 | 400 | 0.0% | 0.00% | 0.0 | 72 | 72 |
| L1 replies | 64 | 105 | 400 | 0.0% | 42.25% | -39.8 | 89 | 57 |
| L2 +tickets | 140 | 210 | 326 | 15.0% | 87.50% | -98.2 | 104 | 28 |
| L3 +address | 166 | 237 | 229 | 26.8% | 100.75% | -110.3 | 106 | 17 |
| L4 +plan | 173 | 259 | 201 | 31.0% | 108.00% | -119.6 | 108 | 16 |
| L5 +refund<=$50 | 202 | 311 | 70 | 42.8% | 128.25% | -140.8 | 114 | 8 |
| L6 +refund<=$500 | 205 | 346 | 35 | 43.0% | 137.75% | -154.1 | 114 | 5 |
| L7 full | 211 | 381 | 0 | 41.8% | 148.00% | -168.2 | 115 | 0 |

![guards](guards_effect.svg)

## Why the agent was held at L7 full (guards on)

| reason | count |
|---|---|
| refund differs from the total the customer requested | 132 |
| company rule: refund > $200 | 105 |
| instruction-injection pattern in email | 88 |
| identity not verified | 81 |
| company rule: cancellations need a human | 63 |

Every execution attempt (including timeouts, retries, and dedup hits) is in `events_L*.jsonl`.
