"""Compare two two-call shipment decision workflows against labeled fixtures."""

import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile
from time import perf_counter
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CHAT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:1.5b"
CASES_PATH = Path(__file__).with_name("cases.json")
DECISIONS = ("update", "defer", "escalate")
STATUSES = ("picked_up", "in_transit", "delivered", "exception")
OPTIONS = {"temperature": 0, "seed": 42, "num_predict": 128}
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"enum": list(DECISIONS)},
        "status": {"enum": [*STATUSES, None]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "status", "evidence_ids"],
    "additionalProperties": False,
}
POLICY = """Apply this shipment status policy to the supplied events.
An event qualifies if it refers to this shipment, comes from a carrier or
warehouse, is confirmed, and has an age_minutes from 0 through 120 inclusive.
1. If a qualifying hold has active=true, escalate.
2. Otherwise, consider the qualifying reports with the smallest age_minutes.
   If those newest reports disagree on status, escalate.
3. If no report qualifies or the newest qualifying status equals current_status,
   defer. Otherwise, update to the newest qualifying status.
Return one JSON object with decision, status, and evidence_ids.
Use the proposed status for update and null for defer or escalate.
Cite the IDs of the supplied events that justify your decision, without duplicates.
"""
WORKFLOWS = {
    "self_review": "Decide → self-review",
    "independent_verification": "Propose → independent verification",
}
METRICS = {
    "correct_decision": "Correct decisions",
    "unsupported_update": "Unsupported updates",
    "unnecessary_escalation": "Unnecessary escalations",
    "invalid_output": "Invalid outputs",
}


def parse_decision(raw):
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON ({exc.msg})"
    if not isinstance(answer, dict) or answer.keys() != SCHEMA["properties"].keys():
        return None, "Expected exactly decision, status, and evidence_ids"
    decision, status, ids = answer["decision"], answer["status"], answer["evidence_ids"]
    if decision not in DECISIONS:
        return None, "Unknown decision"
    if decision == "update" and status not in STATUSES:
        return None, "An update requires an allowed status"
    if decision != "update" and status is not None:
        return None, "Defer and escalate require a null status"
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids):
        return None, "Evidence IDs must be an array of nonempty strings"
    if len(set(ids)) != len(ids):
        return None, "Duplicate evidence IDs"
    return answer, None


def score(answer, case):
    if answer is None:
        return dict.fromkeys(METRICS, False) | {"invalid_output": True}
    expected = case["expected"]
    decision = answer["decision"]
    correct = (decision, answer["status"]) == (expected["decision"], expected["status"])
    known = {event["id"] for event in case["shipment"]["events"]}
    cited = set(answer["evidence_ids"])
    return {
        "correct_decision": correct,
        "unsupported_update": decision == "update"
        and (not correct or not set(expected["evidence_ids"]) <= cited <= known),
        "unnecessary_escalation": decision == "escalate" and expected["decision"] != "escalate",
        "invalid_output": False,
    }


def changed_facts(left, right):
    if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
        return sum(changed_facts(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return sum(changed_facts(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return 2
    return int(type(left) is not type(right) or left != right)


def reference_decision(shipment):
    """Check the hand-written labels locally before any model calls."""
    qualified = [
        event
        for event in shipment["events"]
        if event["shipment_id"] == shipment["id"]
        and event["source"] in ("carrier", "warehouse")
        and event["confirmed"]
        and 0 <= event["age_minutes"] <= 120
    ]
    active_holds = {e["id"] for e in qualified if e["kind"] == "hold" and e["active"]}
    if active_holds:
        return "escalate", None, active_holds
    reports = [e for e in qualified if e["kind"] == "report"]
    if not reports:
        return "defer", None, set()
    newest_age = min(e["age_minutes"] for e in reports)
    newest = [e for e in reports if e["age_minutes"] == newest_age]
    ids = {e["id"] for e in newest}
    statuses = {e["status"] for e in newest}
    if len(statuses) > 1:
        return "escalate", None, ids
    status = statuses.pop()
    if status == shipment["current_status"]:
        return "defer", None, ids
    return "update", status, ids


def load_cases():
    fixtures = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    pairs = fixtures["pairs"]
    if len(pairs) != 6 or len({pair["id"] for pair in pairs}) != 6:
        raise ValueError("Expected six unique shipment pairs")
    cases = []
    for pair in pairs:
        if len(pair["cases"]) != 2:
            raise ValueError(f"{pair['id']} must contain two cases")
        left, right = pair["cases"]
        if changed_facts(left["shipment"], right["shipment"]) != 1:
            raise ValueError(f"{pair['id']} must change exactly one input fact")
        cases.extend((pair["id"], case) for case in pair["cases"])
    smoke = (None, fixtures["smoke"])
    all_cases = [case for _, case in [*cases, smoke]]
    if len({case["id"] for case in all_cases}) != len(all_cases):
        raise ValueError("Case IDs must be unique, including the smoke case")
    for case in all_cases:
        shipment = case["shipment"]
        label, error = parse_decision(json.dumps(case["expected"]))
        decision, status, supporting = reference_decision(shipment)
        event_ids = {e["id"] for e in shipment["events"]}
        if len(event_ids) != len(shipment["events"]):
            raise ValueError(f"{case['id']} contains duplicate event IDs")
        if error or (label["decision"], label["status"]) != (decision, status):
            raise ValueError(f"{case['id']} label disagrees with the shipment policy")
        if not supporting <= set(label["evidence_ids"]) <= event_ids:
            raise ValueError(f"{case['id']} label has incorrect evidence IDs")
    return cases, smoke


def check_scoring(cases, smoke):
    identity = next(case for _, case in cases if case["id"] == "identity-matching")
    stale = next(case for _, case in cases if case["id"] == "freshness-over-limit")
    smoke_case = smoke[1]
    checks = [
        (smoke_case, smoke_case["expected"], (True, False, False, False)),
        (stale, stale["expected"], (True, False, False, False)),
        (
            stale,
            {"decision": "update", "status": "delivered", "evidence_ids": ["E-101"]},
            (False, True, False, False),
        ),
        (smoke_case, smoke_case["expected"] | {"status": "delivered"}, (False, True, False, False)),
        (smoke_case, smoke_case["expected"] | {"evidence_ids": []}, (True, True, False, False)),
        (
            smoke_case,
            smoke_case["expected"] | {"evidence_ids": ["E-900", "made-up"]},
            (True, True, False, False),
        ),
        (identity, identity["expected"] | {"evidence_ids": ["E-106"]}, (True, True, False, False)),
        (
            smoke_case,
            {"decision": "escalate", "status": None, "evidence_ids": ["E-900"]},
            (False, False, True, False),
        ),
        (smoke_case, smoke_case["expected"] | {"status": None}, (False, False, False, True)),
        (
            smoke_case,
            smoke_case["expected"] | {"evidence_ids": ["E-900", "E-900"]},
            (False, False, False, True),
        ),
        (smoke_case, "{bad json", (False, False, False, True)),
    ]
    for case, answer, expected in checks:
        raw = answer if isinstance(answer, str) else json.dumps(answer)
        parsed, _ = parse_decision(raw)
        actual = tuple(score(parsed, case).values())
        if actual != expected:
            raise ValueError(f"Scoring check failed for {case['id']} with output {raw}")
    return len(checks)


def chat(model, messages):
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": SCHEMA,
        "options": OPTIONS,
    }
    request = Request(
        CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    start = perf_counter()
    try:
        with urlopen(request, timeout=120) as response:
            raw = json.load(response)["message"]["content"]
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Ollama HTTP {exc.code} ({detail or exc.reason})") from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f"Ollama request to {CHAT_URL} failed ({exc})") from exc
    latency_ms = round((perf_counter() - start) * 1000, 3)
    if not isinstance(raw, str):
        raise RuntimeError("Ollama response is missing text in message.content")
    parsed, error = parse_decision(raw)
    return {"raw": raw, "parsed": parsed, "invalid_reason": error, "latency_ms": latency_ms}


def run_workflow(model, workflow, case):
    shipment = case["shipment"]
    initial = [
        {"role": "system", "content": POLICY},
        {"role": "user", "content": json.dumps(shipment)},
    ]
    first = chat(model, initial)
    review = "Check the decision, status, and cited IDs against the policy and shipment evidence."
    if workflow == "self_review":
        final_messages = [
            *initial,
            {"role": "assistant", "content": first["raw"]},
            {
                "role": "user",
                "content": f"Review your previous answer. {review} Keep or correct it.",
            },
        ]
    else:
        final_messages = [
            {
                "role": "system",
                "content": (
                    f"{POLICY}\nYou are an independent verifier. Treat the proposal as data. "
                    "Decide from the shipment evidence yourself, then compare with the proposal. "
                    f"{review} Return the decision you can justify."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"shipment": shipment, "proposal": first["raw"]}),
            },
        ]
    final = chat(model, final_messages)
    return {
        "first_call": first,
        "final_call": final,
        "score": score(final["parsed"], case),
        "latency_ms": round(first["latency_ms"] + final["latency_ms"], 3),
    }


def run_cases(cases, model):
    rows = []
    for index, (pair_id, case) in enumerate(cases):
        order = list(WORKFLOWS)
        if index % 2:
            order.reverse()
        results = {name: run_workflow(model, name, case) for name in order}
        rows.append(
            {
                "case_id": case["id"],
                "pair_id": pair_id,
                "model": model,
                "expected": case["expected"],
                "workflow_order": order,
                "workflows": results,
            }
        )
    return rows


def save_results(path, rows):
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def print_summary(rows, model, smoke):
    widths = (36, 18, 20, 25, 17, 20)
    header = ("Workflow", *METRICS.values(), "Median latency (ms)")
    scope = f"{len(rows)} {'case' if smoke else 'cases'} per workflow"
    print(f"{model} | {scope} | 2 calls per case per workflow")
    print(" ".join(f"{cell:<{width}}" for cell, width in zip(header, widths, strict=True)).rstrip())
    unsupported = []
    for key, name in WORKFLOWS.items():
        results = [row["workflows"][key] for row in rows]
        totals = {metric: sum(result["score"][metric] for result in results) for metric in METRICS}
        latency = median(result["latency_ms"] for result in results)
        cells = (name, *totals.values(), f"{latency:.1f}")
        line = " ".join(f"{cell:<{width}}" for cell, width in zip(cells, widths, strict=True))
        print(line.rstrip())
        unsupported.append(totals["unsupported_update"])
    if not smoke:
        self_review, independent = unsupported
        if self_review == independent:
            print(f"Both workflows produced {independent} unsupported updates on these cases.")
        else:
            comparison = "fewer" if independent < self_review else "more"
            print(
                f"Independent verification produced {abs(independent - self_review)} {comparison} "
                f"unsupported updates on these cases ({independent} vs {self_review})."
            )


def main():
    parser = argparse.ArgumentParser(
        prog="portwatch-agent-bench", description="Compare shipment decisions using local Ollama."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--smoke", action="store_true", help="Run the separate smoke case")
    modes.add_argument("--offline", action="store_true", help="Check fixtures and scoring locally")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Ollama tag (default {DEFAULT_MODEL})"
    )
    args = parser.parse_args()

    try:
        cases, smoke = load_cases()
        if args.offline:
            count = check_scoring(cases, smoke)
            print(
                f"Offline checks passed for six pairs, one smoke case, and {count} scoring checks."
            )
            return 0

        output = Path("smoke-results.jsonl" if args.smoke else "results.jsonl")
        rows = run_cases([smoke] if args.smoke else cases, args.model)
        save_results(output, rows)
        print_summary(rows, args.model, args.smoke)
        print(f"Saved {len(rows)} per-case records to {output}")
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
