"""Turn a run_cases --out file into focused evidence for the coding agent.

Usage: python builder/analyze_results.py results/run.json
Writes results/run.evidence.json and prints its path.
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT / "kit" / "build-the-builder"


def load_env():
    for path in (KIT / "env.sh", ROOT / "env.sh"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def categories(reasons):
    found = set()
    for reason in reasons:
        lower = reason.lower()
        if not lower.startswith("conversation:"):
            found.add("ledger")
        else:
            found.add("policy")
            if any(word in lower for word in ("recorded", "successful", "tool fail", "service error")):
                found.add("honesty")
    return sorted(found)


def compact_call(call):
    transcript, events = [], []
    for message in call.get("messages") or []:
        role = message.get("role")
        text = str(message.get("message") or "")
        if role in ("assistant", "user"):
            transcript.append({
                "role": role,
                "node": message.get("nodeId"),
                "text": text[:2000],
            })
            continue
        if role != "system" or not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if event.get("event") not in (
            "node_enter",
            "route_evaluated",
            "function_call_start",
            "function_call_complete",
        ):
            continue
        if event.get("event") == "route_evaluated":
            event.pop("variable_table", None)
        events.append({"node": message.get("nodeId"), **event})
    return {
        "ended_reason": call.get("endedReason"),
        "success_criteria": call.get("successCriteriaResult"),
        "transcript": transcript,
        "events": events,
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    result_path = pathlib.Path(sys.argv[1]).resolve()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    failed = [row for row in rows if not row.get("pass")]
    load_env()
    sys.path.insert(0, str(KIT))
    import freya

    evidence = {
        "agent_id": payload.get("agent_id"),
        "simulation_id": payload.get("simulation_id"),
        "score": {"passed": payload.get("passed"), "total": payload.get("total")},
        "failed": [],
    }
    for row in failed:
        item = {
            "id": row.get("id"),
            "family": row.get("family"),
            "categories": categories(row.get("failed") or []),
            "reasons": row.get("failed") or [],
            "tool_sequence": row.get("tools") or [],
            "call_id": row.get("call_id"),
        }
        if row.get("call_id"):
            try:
                item["call"] = compact_call(freya.get_call(row["call_id"]))
            except Exception as error:
                item["call_error"] = str(error)[:500]
        evidence["failed"].append(item)
    output = result_path.with_suffix(".evidence.json")
    output.write_text(json.dumps(evidence, indent=1, ensure_ascii=False), encoding="utf-8")
    print(output)
    for item in evidence["failed"]:
        print(f"{item['id']}: {', '.join(item['categories'])} | {' > '.join(item['tool_sequence'])}")


if __name__ == "__main__":
    main()
