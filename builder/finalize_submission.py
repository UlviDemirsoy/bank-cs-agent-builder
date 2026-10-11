"""Create submission.json after the final builder run.

Example:
  python3 builder/finalize_submission.py --team TEAM \
    --member "Name,email@example.com" --repo https://github.com/org/repo \
    --mock-url https://worker.workers.dev
"""

import argparse
import json
import pathlib
import subprocess
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent


def http_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise argparse.ArgumentTypeError("must be an HTTP(S) URL")
    return value.rstrip("/")


def parse_member(value):
    try:
        name, email = (part.strip() for part in value.split(",", 1))
    except ValueError as error:
        raise argparse.ArgumentTypeError("use NAME,EMAIL") from error
    if not name or "@" not in email:
        raise argparse.ArgumentTypeError("use NAME,EMAIL")
    return {"name": name, "email": email}


def git_commit():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", required=True)
    parser.add_argument("--member", action="append", type=parse_member, required=True)
    parser.add_argument("--repo", required=True, type=http_url)
    parser.add_argument("--mock-url", required=True, type=http_url)
    args = parser.parse_args()

    workflow = ROOT / "submission" / "final.workflow.json"
    trace = ROOT / "submission" / "final.trace.jsonl"
    missing = [str(path.relative_to(ROOT)) for path in (workflow, trace) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing final artifact(s): {', '.join(missing)}")

    submission = {
        "team": args.team,
        "members": args.member,
        "repo": args.repo,
        "commit": git_commit(),
        "run_command": "python3 builder/build.py {case_folder} {mock_url}",
        "workflow": "submission/final.workflow.json",
        "trace": "submission/final.trace.jsonl",
        "mock_url": args.mock_url,
    }
    destination = ROOT / "submission.json"
    destination.write_text(
        json.dumps(submission, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()
