"""Generate the final bank mock from the supplied case folder.

Usage:
    python3 builder/build_mock.py CASE_FOLDER
"""

import asyncio
import dataclasses
import json
import os
import pathlib
import sys
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")


def load_env():
    for path in (ROOT / "kit" / "build-the-builder" / "env.sh", ROOT / "env.sh"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("export ")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def purpose(message):
    actions = []
    text = ""
    for block in message.content:
        if isinstance(block, ToolUseBlock):
            actions.append(block.name)
        elif isinstance(block, TextBlock) and not text:
            text = " ".join(block.text.split())
    return ("; ".join(actions) or text or "continue mock generation")[:240]


async def build_mock(case_folder):
    trace_dir = ROOT / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = trace_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-mock.jsonl"
    prompt = f"""
Build the bank-service mock for the final Freya case in exactly "{case_folder}".
Recursively discover that folder and read its README first, then the complete service
contract and every visible test-customer fixture. These fixtures are allowed here:
the mock must start with their bank state. Do not read old workflows, results, traces,
credentials, env files, or unrelated practice cases.

Read mixed formats structurally. Inspect diagrams in PDFs visually and render relevant
pages to images if text extraction loses arrows or box relationships. Extract DOCX
headings/tables and XLSX sheets/cells, preserving versions, field names and types.

Implement the contract in mock/src/generated.ts using the existing MockRequest,
MockResponse, and StateStore interfaces. Preserve every path, HTTP method, request
field, validation rule, response shape, status code, and state transition from the
contract. Route each request to a stable customer key without putting mutable state
in module globals. Seed each visible customer lazily and persist all mutations through
StateStore. Make retries idempotent where the contract requires it. Unknown routes
must return contract-appropriate JSON errors, never invented success.

Do not edit mock/src/index.ts or wrangler.jsonc unless the supplied contract truly
requires an infrastructure change. Do not deploy. Run npm install, npm run cf-typegen,
npm run typecheck, and npm run deploy:dry from mock/. Finish with the implemented
endpoint count and validation results.
""".strip()
    options = ClaudeAgentOptions(
        cwd=ROOT,
        model=MODEL,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="dontAsk",
        max_turns=100,
        env={
            "BASH_DEFAULT_TIMEOUT_MS": "900000",
            "BASH_MAX_TIMEOUT_MS": "1200000",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        },
    )
    with trace.open("w", encoding="utf-8") as output:
        output.write(json.dumps({
            "start": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "case_folder": str(case_folder),
            "model": MODEL,
        }) + "\n")
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                output.write(json.dumps({
                    "type": "model_call",
                    "model": message.model,
                    "purpose": purpose(message),
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
            elif isinstance(message, ResultMessage):
                print(f"done: {message.num_turns} turns", flush=True)
        output.write(json.dumps({
            "end": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "generated": str(ROOT / "mock" / "src" / "generated.ts"),
        }) + "\n")
    print(f"mock trace: {trace}", flush=True)


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        raise SystemExit(__doc__)
    case_folder = pathlib.Path(sys.argv[1]).expanduser().resolve()
    if not case_folder.is_dir():
        raise SystemExit(f"case folder not found: {case_folder}")
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required")
    asyncio.run(build_mock(case_folder))


if __name__ == "__main__":
    main()
