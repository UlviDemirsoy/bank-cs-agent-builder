"""Build Freya phone agents with a Claude coding-agent loop.

Practice:
    python3 builder/build.py briefs/brief-a.md tools/openapi.json
    python3 builder/build.py briefs/ tools/openapi.json

Final:
    python3 builder/build.py CASE_FOLDER https://your-mock.workers.dev

Each brief gets one stateful Claude Agent SDK session. Claude reads the files,
writes the workflow, saves it, runs the practice cases in the foreground, and
edits the same file from the FAIL output. No phone agent is edited by hand.
"""
import asyncio
import dataclasses
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.parse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT / "kit" / "build-the-builder"
MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
BUDGET_SECONDS = 30 * 60
AGENT_BUDGET_SECONDS = BUDGET_SECONDS - 4 * 60


def is_http_url(value):
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


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


def openapi_for(brief, fallback):
    sibling = brief.parent / f"{brief.stem}.openapi.json"
    if sibling.exists():
        return sibling
    if fallback and fallback.exists():
        return fallback
    default = KIT / "tools" / "openapi.json"
    if default.exists():
        return default
    raise RuntimeError(f"no OpenAPI file for {brief.name}")


def jobs_from_directory(folder, fallback):
    files = sorted(path for path in folder.glob("*.md") if path.is_file())
    if not files:
        raise RuntimeError(f"no briefs in {folder}")
    jobs = []
    for target in files:
        target_text = target.read_text(encoding="utf-8").lower()
        dependencies = []
        for other in files:
            if other == target:
                continue
            other_text = other.read_text(encoding="utf-8")
            stem = other.stem.replace("_", " ").replace("-", " ").lower()
            heading = next(
                (line.lstrip("#").strip().lower() for line in other_text.splitlines() if line.startswith("#")),
                "",
            )
            if stem in target_text or (heading and heading in target_text):
                dependencies.append(other)
        jobs.append((dependencies + [target], openapi_for(target, fallback)))
    return jobs


def parse_args(argv):
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(__doc__)
    paths = [pathlib.Path(arg).resolve() for arg in argv]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise RuntimeError(f"missing file: {missing[0]}")
    directories = [path for path in paths if path.is_dir()]
    files = [path for path in paths if path.is_file()]
    briefs = [path for path in files if path.suffix.lower() != ".json"]
    apis = [path for path in files if path.suffix.lower() == ".json"]
    fallback = apis[-1] if apis else None
    if directories:
        return jobs_from_directory(directories[0], fallback)
    if not briefs or not fallback:
        raise RuntimeError("a brief and an OpenAPI JSON file are required")
    return [(briefs, fallback)]


def practice_label(brief):
    cases_path = KIT / "public-test-set.json"
    briefs_dir = KIT / "briefs"
    if not cases_path.exists() or not briefs_dir.exists():
        return ""
    known = {
        case.get("brief")
        for case in json.loads(cases_path.read_text(encoding="utf-8"))
        if case.get("brief")
    }
    text = brief.read_text(encoding="utf-8").strip()
    for practice in briefs_dir.glob("*.md"):
        if practice.resolve() != brief.resolve() and practice.read_text(encoding="utf-8").strip() != text:
            continue
        label = practice.stem.split("-")[-1].split("_")[-1].upper()
        if label in known:
            return label
    return ""


def export_result(slot):
    state_file = ROOT / ".freya-agents.json"
    if not state_file.exists():
        raise RuntimeError(f"agent did not save slot {slot}")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if slot not in state:
        raise RuntimeError(f"agent did not save slot {slot}")
    saved = state[slot]
    sys.path.insert(0, str(KIT))
    import freya

    submission = ROOT / "submission" / f"{slot}.workflow.json"
    submission.parent.mkdir(parents=True, exist_ok=True)
    freya.export_workflow(saved["workflow_id"], str(submission))
    source = KIT / "out" / f"{slot}.workflow.json"
    if source.exists():
        destination = ROOT / "out" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return saved, submission


def keep_one_learned_question(before):
    path = ROOT / "builder" / "learned-questions.md"
    after = path.read_text(encoding="utf-8") if path.exists() else before
    if after == before:
        return ""
    if not after.startswith(before):
        path.write_text(before, encoding="utf-8")
        return ""
    additions = [
        line.strip()
        for line in after[len(before):].splitlines()
        if line.strip().startswith("- ")
    ]
    existing = {" ".join(line.lower().split()) for line in before.splitlines() if line.startswith("- ")}
    valid = []
    for line in additions:
        normalized = " ".join(line.lower().split())
        if normalized in existing or len(line) > 280 or not line.endswith("?"):
            continue
        if re.search(r"\d|PUB-|CASE[-_ ]|\$|€|£", line, re.I):
            continue
        if not re.match(r"^- (does|do|is|are|can|could|should|when|if|has|have|will)\b", line, re.I):
            continue
        valid.append(line)
    question = valid[0] if valid else ""
    path.write_text(before.rstrip() + (f"\n{question}\n" if question else "\n"), encoding="utf-8")
    return question


def trace_purpose(message):
    """Return a concise purpose line for each Claude model response."""
    actions = []
    text = ""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                detail = block.input.get("description") if isinstance(block.input, dict) else None
                actions.append(f"{block.name}: {detail}" if detail else block.name)
            elif isinstance(block, TextBlock) and not text:
                text = " ".join(block.text.split())
    purpose = "; ".join(actions) or text or "continue builder reasoning"
    return purpose[:240]


def workflow_stats(path):
    exported = json.loads(path.read_text(encoding="utf-8"))
    workflow = exported.get("workflow") or exported
    nodes = workflow.get("nodes") or {}
    tools = list(workflow.get("tools") or [])
    for node in nodes.values():
        tools.extend(node.get("tools") or [])
        tools.extend(node.get("pre_actions") or [])
    return len(nodes), sum(
        1 for tool in tools if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    )


async def build_one(briefs=None, openapi=None, case_folder=None, mock_url=None):
    final_mode = case_folder is not None
    slot = "final" if final_mode else briefs[-1].stem
    label = "" if final_mode else practice_label(briefs[-1])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trace = ROOT / "traces" / f"{stamp}-{slot}.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    learned_path = ROOT / "builder" / "learned-questions.md"
    learned_before = learned_path.read_text(encoding="utf-8") if learned_path.exists() else ""
    if final_mode:
        prompt = (
            "Use the build-phone-agent skill, adapted for the final case. The complete case folder is exactly "
            f"\"{case_folder}\" and the deployed bank mock base URL is exactly \"{mock_url}\".\n"
            "Start with the case README and recursively discover the entire case folder. Read the bank evidence, "
            "analysis, flow, announcements, questions and answers, example conversations, and service contract. "
            "Treat mixed document formats as first-class evidence: inspect diagram PDFs visually, and if plain-text "
            "extraction loses arrows or box relationships, render each relevant page to an image and inspect that "
            "image. Extract DOCX headings/tables and XLSX sheets/cells structurally rather than relying on filenames. "
            "Record source date/version and resolve contradictions using the case's authority and recency evidence. "
            "Before the first workflow write, do not read files identified by the case README as test customers, "
            "test expectations, or evaluation fixtures. Do not read any old workflow, result, or trace.\n"
            "Derive a normalized OpenAPI 3 JSON contract at out/final.openapi.json from the supplied service "
            "contract. Preserve every path, method, field, required flag, type and enum, but set its server base "
            f"to the mock URL \"{mock_url}\". Every api_call tool in the workflow must use that URL; never use the "
            "practice freya-bank-mock URL.\n"
            "Write the phone agent blind to out/final.workflow.json, perform the skill's two requirement-coverage "
            "passes, run ../../builder/preflight.py out/final.workflow.json out/final.openapi.json, and run the "
            "Freya dry-run. Save slot final with state_file='../../.freya-agents.json'.\n"
            "Only after that first save, read the visible test customers. Follow the case README's test method. "
            "If no runner is supplied, create temporary personas/sessions from those visible customers, run text "
            "simulations against the saved agent, and score both the mock's recorded state and conversation policy. "
            "Keep tests foreground, save result/evidence files, edit the same workflow only from evidence, and use "
            "at most three fix cycles. Never run public A/B practice cases for the final.\n"
            "Do not run repeated durability tests. Agent work stops at 26 minutes so export and trace can finish. "
            "Do not read env.sh, .env files, credentials, API keys, hidden customers, old results, or old traces. "
            "If a fix teaches a reusable lesson, append one sanitized brief-independent question to "
            "../../builder/learned-questions.md. Finish with workflow id, agent id, node/tool counts, and last score."
        )
    else:
        brief_args = " ".join(f'"{path}"' for path in briefs)
        test_instruction = (
            f"These are the kit practice customers for label {label}. Run them in the foreground."
            if label
            else "No practice customers belong to this brief. Do not run public A or B cases against it."
        )
        prompt = (
            "Use the build-phone-agent skill. Build the phone agent for these brief files, in this order: "
            f"{brief_args}\nThe OpenAPI file for this run is exactly: \"{openapi}\". "
            "Do not substitute tools/openapi.json unless it is that exact file.\n"
            f"The slot is {slot}. Write out/{slot}.workflow.json. "
            "Save with freya.save_workflow using state_file='../../.freya-agents.json'. "
            f"{test_instruction}\n"
            "At the start of this run, read README.md and VERSION, discover docs/**/* with Glob, and read the "
            "relevant current platform docs; do not assume fixed document names or a remembered Freya schema. "
            "Keep every test command in the foreground. Read every FAIL and edit the existing workflow file; "
            "do not regenerate a working file wholesale. Use at most three test-fix cycles. "
            "Do not run repeated durability tests; agent work stops at 26 minutes so export and trace can finish. "
            "Before the first save, do not inspect public-test-set.json, old results, traces, or practice definitions. "
            f"Do not read an existing out/{slot}.workflow.json before its first write. "
            f"Run ../../builder/preflight.py out/{slot}.workflow.json \"{openapi}\" before dry-run/save. "
            "Give every run_cases call an --out file; "
            "on failure run ../../builder/analyze_results.py and fix only from that evidence. "
            "If a test-driven edit teaches a reusable lesson, append exactly one brief-independent question to "
            "../../builder/learned-questions.md, with no values or case identifiers. "
            "Do not read env.sh, .env files, credentials, or API keys. "
            "Finish with the workflow id, agent id, and last score."
        )
    options = ClaudeAgentOptions(
        cwd=KIT,
        model=MODEL,
        skills=["build-phone-agent"],
        setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="dontAsk",
        max_turns=150,
        env={
            "BASH_DEFAULT_TIMEOUT_MS": "900000",
            "BASH_MAX_TIMEOUT_MS": "1200000",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        },
    )
    started = time.time()
    with trace.open("w", encoding="utf-8") as output:
        output.write(json.dumps({
            "start": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "briefs": [str(path) for path in briefs] if briefs else None,
            "case_folder": str(case_folder) if case_folder else None,
            "openapi": str(openapi) if openapi else None,
            "mock_url": mock_url,
            "model": MODEL,
        }) + "\n")
        output.write(json.dumps({
            "type": "builder_prompt",
            "prompt": prompt,
        }, ensure_ascii=False) + "\n")
        output.flush()
        try:
            async with asyncio.timeout(AGENT_BUDGET_SECONDS):
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        output.write(json.dumps({
                            "type": "model_call",
                            "model": message.model,
                            "purpose": trace_purpose(message),
                        }, ensure_ascii=False) + "\n")
                    output.write(json.dumps(
                        {"type": type(message).__name__, **dataclasses.asdict(message)},
                        default=str,
                        ensure_ascii=False,
                    ) + "\n")
                    output.flush()
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                print(block.text, flush=True)
                            elif isinstance(block, ToolUseBlock):
                                print(f"  [{block.name}] {str(block.input)[:160]}", flush=True)
                    elif isinstance(message, ResultMessage):
                        print(
                            f"done: {message.num_turns} turns, "
                            f"{message.duration_ms / 1000:.0f} s, "
                            f"${message.total_cost_usd or 0:.2f}",
                            flush=True,
                        )
        except TimeoutError:
            output.write(json.dumps({
                "phase": "agent_timeout",
                "seconds": AGENT_BUDGET_SECONDS,
                "reserved_seconds": BUDGET_SECONDS - AGENT_BUDGET_SECONDS,
            }) + "\n")
            print("agent work stopped at 26 minutes; reserving time for learning, export and trace", flush=True)
        learned = keep_one_learned_question(learned_before)
        output.write(json.dumps({"phase": "self_improve", "question": learned or None}) + "\n")
        saved, submission = export_result(slot)
        step_count, tool_count = workflow_stats(submission)
        output.write(json.dumps({
            "end": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "seconds": round(time.time() - started),
            "workflow_id": saved["workflow_id"],
            "agent_id": saved["agent_id"],
            "steps": step_count,
            "tools": tool_count,
            "export": str(submission),
        }) + "\n")
    if final_mode:
        shutil.copyfile(trace, ROOT / "submission" / "final.trace.jsonl")
    print(f"exported {submission}", flush=True)


async def async_main(argv):
    if (
        len(argv) == 2
        and pathlib.Path(argv[0]).expanduser().is_dir()
        and is_http_url(argv[1])
    ):
        case_folder = pathlib.Path(argv[0]).expanduser().resolve()
        mock_url = argv[1].rstrip("/")
        print(f"\n=== final: {case_folder.name} ===", flush=True)
        await build_one(case_folder=case_folder, mock_url=mock_url)
        return
    for briefs, openapi in parse_args(argv):
        print(f"\n=== {briefs[-1].name} ===", flush=True)
        await build_one(briefs, openapi)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    if not os.environ.get("FREYA_API_KEY") or not os.environ.get("FREYA_BANK_TEAM_KEY"):
        raise SystemExit("FREYA_API_KEY and FREYA_BANK_TEAM_KEY are required")
    asyncio.run(async_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
