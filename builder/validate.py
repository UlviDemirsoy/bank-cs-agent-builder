"""Run durability validation separately from the 30-minute builder.

Usage:
    python builder/validate.py AGENT_ID --brief A
    python builder/validate.py AGENT_ID --brief B --reps 3 --only PUB-11
"""

import argparse
import os
import pathlib
import runpy
import sys
import time

import build


ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT / "kit" / "build-the-builder"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_id")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--only")
    parser.add_argument("--out")
    args = parser.parse_args()

    build.load_env()
    output = args.out or f"results/{args.brief.lower()}-validate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    forwarded = [
        "tools/run_cases.py",
        args.agent_id,
        "--brief",
        args.brief,
        "--reps",
        str(args.reps),
        "--out",
        output,
    ]
    if args.only:
        forwarded.extend(["--only", args.only])

    os.chdir(KIT)
    sys.argv = forwarded
    runpy.run_path("tools/run_cases.py", run_name="__main__")


if __name__ == "__main__":
    main()
