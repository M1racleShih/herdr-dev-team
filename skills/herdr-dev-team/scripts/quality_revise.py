"""Activate a six-pack plan revision and its replacement assignment atomically."""

import json

import four_pack
import quality_store
from pack_store import (
    accepted_receipt, assignment_id, assignment_key, check_dispatch_project, current_assignment,
    get_run, insert_assignment, nonempty, touch_run, transaction,
)


ROLE_ORDER = ("specifier", "coder", "cleaner", "architect", "hardener", "qa")
TOOL_FIELDS = {"argv", "protocol", "fail_codes", "timeout_seconds"}


def affected_roles(old, new, kind):
    if old["qa_command"] != new["qa_command"]:
        raise ValueError("qa_command is fixed for the entire run")
    affected = set()
    for reference, role in (("approved_examples_check", "coder"), ("hardening_check", "hardener")):
        if old[reference] != new[reference]:
            if kind == "tool":
                raise ValueError("changing a required check reference needs an authorized policy revision")
            affected.add(role)
    before, after = ({c["id"]: c for c in plan["checks"]} for plan in (old, new))
    changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
    if kind == "tool" and len(changed) > 1:
        raise ValueError("a tool revision replaces the execution settings of a single check")
    for key in changed:
        prior, updated = before.get(key, {}), after.get(key, {})
        fields = {field for field in prior.keys() | updated.keys() if prior.get(field) != updated.get(field)}
        if kind == "tool" and fields - TOOL_FIELDS:
            raise ValueError(f"changing check {key} beyond its execution settings needs an authorized policy revision")
        affected.update(row["owner"] for row in (prior, updated) if row)
    if not affected:
        raise ValueError("the revision does not change any check")
    return affected


def settled_delivery(db, store, run_id, dispatch_lock):
    # An authenticated accepted receipt resolves whether a message arrived. A live
    # sender still has to release its inherited lock before a new dispatch starts.
    rows = db.execute("""
        SELECT DISTINCT a.id FROM assignments a JOIN attempts t ON t.assignment_id = a.id
        WHERE a.run_id = ? AND t.status IN ('intent', 'ambiguous')
          AND t.number = (SELECT MAX(number) FROM attempts WHERE assignment_id = a.id)
    """, (run_id,))
    for row in rows:
        with dispatch_lock(store, row["id"]) as lock:
            if lock is None or not accepted_receipt(db, row["id"]):
                raise ValueError("resolve the uncertain or in-flight delivery before activating a plan")


def restart_assignment(db, run, affected):
    current = current_assignment(db, run)
    receipt = accepted_receipt(db, current["id"])
    if not receipt or receipt["status"] not in ("blocked", "paused"):
        raise ValueError("the current worker must report paused or blocked")
    commit = four_pack.head_at_or_after(run, json.loads(receipt["payload"])["commit"])
    check_dispatch_project(run, commit)
    spec = four_pack.latest_spec(db, run)
    if not spec or not spec["approval"] or current["role"] == "specifier":
        role = "specifier"
    else:
        role = min(affected | {current["role"]}, key=ROLE_ORDER.index)
        if (role == "qa" and not four_pack.qa_repair_finding(db, run, spec, current["round"])
                and commit != four_pack.hardener_commit(db, run, spec, current["round"])):
            role = "coder"
    return role, four_pack.next_round(db, run), commit, receipt["id"]


def activate(db, store, run, args, dispatch_lock):
    plan = quality_store.read_plan(args.plan)
    reason = nonempty(args.reason, "plan revision reason")
    evidence = args.approval_evidence
    if args.kind == "policy" or evidence is not None:
        evidence = nonempty(evidence or "", "project approval evidence")
    base = args.base_version
    with transaction(db):
        run = get_run(db, run["id"], "six")
        active = quality_store.get_plan(db, run["id"])
        if not active:
            raise ValueError("plan-revise requires a run initialized with --plan")
        if active["plan_version"] != base:
            prior = quality_store.get_plan(db, run["id"], base + 1)
            if (prior and prior["base_version"] == base and prior["plan"] == plan
                    and (prior["kind"], prior["reason"], prior["approval_evidence"]) ==
                    (args.kind, reason, evidence)):
                return dict(prior["decision"], duplicate=True), None
            raise ValueError(f"stale plan base {base}; current version is {active['plan_version']}")
        affected = affected_roles(active["plan"], plan, args.kind)
        if run["phase"] not in ("ready", "waiting-for-human", "paused", "blocked"):
            raise ValueError("revise a plan before dispatch, while awaiting approval, or after a paused/blocked receipt")
        quality_store.assert_no_active(db, run["id"])
        settled_delivery(db, store, run["id"], dispatch_lock)
        if four_pack.changed_examples(db, run):
            raise ValueError("examples changed; use rework --specification and submit the new version first")
        restart = restart_assignment(db, run, affected) if run["phase"] in ("paused", "blocked") else None
        decision = {"run": run["id"], "plan_version": base + 1, "phase": run["phase"]}
        item_id = None
        if restart:
            role, number, commit, source = restart
            item_id = assignment_id(run, role, number)
            decision.update(phase="active", next=assignment_key(role, number), assignment=item_id)
        quality_store.record_plan(db, run["id"], base + 1, plan, kind=args.kind, reason=reason,
                                  approval_evidence=evidence, base_version=base, decision=decision)
        if restart:
            insert_assignment(db, run, role, number, commit, source)
        else:
            touch_run(db, run)
        return decision, item_id
