"""Execution harness: policy gate -> idempotent execution with retries -> event log -> metrics.

Human model: a human reviewer sees every held plan, approves the proposed actions that are
correct, rejects the rest, and finishes whatever the gold resolution still needs. Humans are
assumed accurate; their cost appears as intervention time, not as errors.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .agent import Plan, Proposed
from .env import Action, Customer, Email, TransientTimeout, World
from .policy import Policy

MIN_MANUAL = 7.0         # minutes for a human to handle an email from scratch
MIN_REVIEW = 4.0         # minutes to review/finish an agent-drafted plan
MIN_SPOT_CHECK = 0.5     # minutes of sampling QA on autonomous work
MIN_FIX_UNAUTHORIZED = 25.0
MIN_FIX_DUPLICATE = 15.0


def canonical(tool: str, args: Dict) -> Action:
    if tool == "send_reply":
        return ("send_reply", args["cid"])
    if tool == "update_record":
        return ("update_record", args["cid"], args["field"], args["value"])
    if tool == "issue_refund":
        return ("issue_refund", args["cid"], round(float(args["amount"]), 2))
    if tool == "cancel_account":
        return ("cancel_account", args["cid"])
    return ("advance_ticket", args["tid"], args["status"])


def _args_for_world(tool: str, args: Dict) -> Dict:
    a = dict(args)
    if tool == "send_reply":
        a.setdefault("text", "")
    if tool == "update_record":
        a["fld"] = a.pop("field")
    return a


@dataclass
class Metrics:
    level: str
    guards: bool
    emails: int = 0
    duplicate_actions: int = 0
    unauthorized_changes: int = 0
    human_interventions: int = 0
    autonomous_completions: int = 0
    minutes: float = 0.0
    timeouts: int = 0
    retries: int = 0
    dedup_hits: int = 0
    hold_reasons: Counter = field(default_factory=Counter)

    @property
    def error_rate(self) -> float:
        return (self.duplicate_actions + self.unauthorized_changes) / max(1, self.emails)

    @property
    def completion_rate(self) -> float:
        return self.autonomous_completions / max(1, self.emails)

    @property
    def hours_saved(self) -> float:
        return (MIN_MANUAL * self.emails - self.minutes) / 60


class Harness:
    def __init__(self, world: World, policy: Policy, log_path: Optional[str] = None, max_retries: int = 3):
        self.world, self.policy, self.max_retries = world, policy, max_retries
        self.log = open(log_path, "w") if log_path else None
        self.done_by_ticket: Dict[str, List[Action]] = {}

    def _event(self, **e) -> None:
        if self.log:
            self.log.write(json.dumps(e, default=str) + "\n")

    def execute(self, email: Email, a: Proposed, actor: str, m: Metrics) -> None:
        act = canonical(a.tool, a.args)
        key = None
        if self.policy.guards or actor == "human":
            # Deterministic key: same ticket + same effect = same key, across retries AND resent emails.
            key = hashlib.sha256(json.dumps([email.ticket_id, act], default=str).encode()).hexdigest()[:16]
        for attempt in range(self.max_retries + 1):
            try:
                res = self.world.call(a.tool, email.eid, idempotency_key=key, **_args_for_world(a.tool, a.args))
                if res == "deduplicated":
                    m.dedup_hits += 1
                self._event(eid=email.eid, actor=actor, action=act, attempt=attempt, result=res, key=key)
                return
            except TransientTimeout:
                m.timeouts += 1
                m.retries += 1
                self._event(eid=email.eid, actor=actor, action=act, attempt=attempt, result="timeout", key=key,
                            backoff_s=0.2 * 2 ** attempt)
        self._event(eid=email.eid, actor=actor, action=act, result="gave_up", key=key)

    def process(self, email: Email, plan: Plan, m: Metrics, gold_by_ticket: Dict[str, List[Action]]) -> None:
        m.emails += 1
        writes_before = len(self.world.writes)
        verdicts = self.policy.check_plan(email, plan)
        held = [a for a, v in zip(plan.actions, verdicts) if v.decision == "human"]
        human = bool(held) or plan.escalate or not plan.actions
        self._event(eid=email.eid, plan=[(a.tool, a.args) for a in plan.actions], verdicts=[v.reason for v in verdicts],
                    resolution=plan.resolution)
        for a, v in zip(plan.actions, verdicts):
            if v.decision == "execute":
                self.execute(email, a, "agent", m)
        gold = gold_by_ticket[email.ticket_id]
        if human:
            m.human_interventions += 1
            for v in verdicts:
                if v.decision == "human":
                    m.hold_reasons[v.reason.replace("held: ", "")] += 1
            if plan.escalate or not plan.actions:
                m.hold_reasons["agent escalated / no plan"] += 1
            # Human approves correct held actions, then completes what the gold resolution still needs.
            done = Counter(a for e, a in self.world.writes if self._ticket(e) == email.ticket_id)
            todo = Counter(gold) - done
            for a in held:
                act = canonical(a.tool, a.args)
                if todo[act] > 0:
                    self.execute(email, a, "human", m)
                    todo[act] -= 1
            for act, n in list(todo.items()):
                for _ in range(n):
                    self._human_do(email, act, m)
            m.minutes += MIN_MANUAL if self.policy.level == 0 else MIN_REVIEW
        else:
            m.minutes += MIN_SPOT_CHECK

        # score this email's writes against its ticket's gold resolution
        new = [a for _, a in self.world.writes[writes_before:]]
        prior = Counter(a for e, a in self.world.writes[:writes_before] if self._ticket(e) == email.ticket_id)
        allowed = Counter(gold)
        dup = unauth = 0
        seen = Counter(prior)
        for a in new:
            seen[a] += 1
            if a not in allowed:
                if seen[a] == 1:
                    unauth += 1
                else:
                    dup += 1
            elif seen[a] > allowed[a]:
                dup += 1
        m.duplicate_actions += dup
        m.unauthorized_changes += unauth
        m.minutes += unauth * MIN_FIX_UNAUTHORIZED + dup * MIN_FIX_DUPLICATE
        complete = Counter(gold) - Counter(a for e, a in self.world.writes if self._ticket(e) == email.ticket_id) == Counter()
        if not human and complete and dup == 0 and unauth == 0:
            m.autonomous_completions += 1

    def _human_do(self, email: Email, act: Action, m: Metrics) -> None:
        tool = act[0]
        if tool == "send_reply":
            args = {"cid": act[1]}
        elif tool == "update_record":
            args = {"cid": act[1], "field": act[2], "value": act[3]}
        elif tool == "issue_refund":
            args = {"cid": act[1], "amount": act[2]}
        elif tool == "cancel_account":
            args = {"cid": act[1]}
        else:
            args = {"tid": act[1], "status": act[2]}
        self.execute(email, Proposed(tool, args), "human", m)

    _ticket_of: Dict[str, str] = {}

    def _ticket(self, eid: str) -> str:
        return self._ticket_of.get(eid, "")


def run(customers: Dict[str, Customer], tickets: Dict[str, str], emails: Sequence[Email], planner, level: int, guards: bool,
        seed: int = 5, log_path: Optional[str] = None, policy_version: int = 2) -> Metrics:
    world = World(customers, tickets, seed=seed)
    policy = Policy(level, guards, policy_version)
    h = Harness(world, policy, log_path)
    h._ticket_of = {e.eid: e.ticket_id for e in emails}
    gold_by_ticket: Dict[str, List[Action]] = {}
    for e in emails:
        if e.scenario != "duplicate_resend":
            gold_by_ticket[e.ticket_id] = list(e.gold_actions)
    m = Metrics(policy.name, guards)
    for e in emails:
        try:
            plan = planner.plan(e, world)
        except RuntimeError as err:  # model refusal / API error -> human handles it
            plan = Plan(None, "unresolved", [], escalate=True, note=str(err))
        h.process(e, plan, m, gold_by_ticket)
    if h.log:
        h.log.close()
    return m


def shadow_precision(customers, tickets, emails, planner) -> Dict[str, List[int]]:
    """L0: log proposals only and score them per capability against gold -> evidence for which permission to grant next."""
    from .policy import capability

    world = World(customers, tickets, seed=0, fault_rate=0.0)
    gold = {e.ticket_id: Counter(e.gold_actions) for e in emails if e.scenario != "duplicate_resend"}
    stats: Dict[str, List[int]] = {}
    for e in emails:
        if e.scenario == "duplicate_resend":
            continue
        plan = planner.plan(e, world)
        for a in plan.actions:
            s = stats.setdefault(capability(a), [0, 0])
            s[1] += 1
            s[0] += int(gold[e.ticket_id][canonical(a.tool, a.args)] > 0)
    return stats
