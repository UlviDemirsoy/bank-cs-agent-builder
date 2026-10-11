---
name: build-phone-agent
description: Build a Freya phone agent from a bank's brief, test it on the practice customers and fix what fails. Use when asked to build or improve a phone agent from a brief.
---

# Build a phone agent from a brief

Work from the kit folder. Use only what the brief and the tool API file say. Do not copy rules from another brief.

## Steps

1. Read `README.md`, `VERSION`, the brief files, the exact OpenAPI file named in the request, and `../../builder/learned-questions.md`.
2. Discover the platform documentation fresh on every run with `Glob docs/**/*`. Read every relevant current document; do not assume document names or rely on a remembered Freya schema. Treat the current starter-kit metadata, discovered docs and exact OpenAPI file as the source of truth for this run.
3. Read `examples/base-agent.json`. It is a small working agent with two steps. Use it as the pattern for the JSON, not as the answer.
4. Write the workflow to `out/<slot>.workflow.json`. `<slot>` is the brief's file name without `.md`. Do not read an existing file for this slot before the first write; rebuild it blind from the inputs.
   - One step per stage of the call. Identity comes first.
   - Move to the next step on a tool result, not on what the customer says.
   - Take every tool field and allowed value from the exact OpenAPI file named in the request.
   - Leave `agent_config` out.
5. Before preflight, perform at most two requirement-coverage passes with the same stateful agent:
   - Pass one: reread the brief and OpenAPI, make a requirement-to-workflow ledger covering every mandatory rule, table row, amendment, exception, tool outcome, retry and terminal path, then fix every omission in the existing JSON.
   - Pass two: reread the source independently of the first conclusion, verify the ledger mappings and every changed area, and fix any remaining omission. Stop after this pass even if uncertain.
   Do not regenerate the JSON and do not start an open-ended review loop. Then run `python ../../builder/preflight.py out/<slot>.workflow.json <the-exact-openapi-path>`, fix every ERROR, review WARN lines against the brief, and dry-run with `freya.validate_workflow`.
6. Save it: `python3 -c "import freya,json,sys; print(json.dumps(freya.save_workflow(sys.argv[1], json.load(open(sys.argv[2])), state_file='../../.freya-agents.json')))" <slot> out/<slot>.workflow.json`
   It prints `workflow_id` and `agent_id`. The same slot updates the same workflow.
7. If the request names a practice label, test it with an output file: `python3 tools/run_cases.py <agent_id> --brief <label> --out results/<slot>-run<N>.json`. A run takes 3 to 11 minutes. Run it in the foreground and wait for it to finish. Never start it in the background and never end your turn while a test is running. If there are no matching practice customers, do not test against A or B.
8. On failure, run `python ../../builder/analyze_results.py results/<slot>-run<N>.json`. Read the evidence file. Change only the workflow part supported by ledger, policy, honesty, transcript, tool or route evidence. Save and retest only failed cases when useful. Do this at most three times.
9. Do not use `--reps` inside a builder run. One full practice run plus targeted failed-case retests must fit the build budget; repeated durability validation is a separate operation.
10. If an evidence-supported edit taught a reusable lesson, distill it into exactly one brief-independent question and append it to `../../builder/learned-questions.md`. Preserve the failure pattern, never the instance: do not include dates, amounts, case ids, outcome names, customer names or expected answers.
11. Finish with one line: the `workflow_id`, the `agent_id` and how many customers passed in the last run. Leave enough time for the caller to export the saved workflow and finish the trace.

## Rules

- Never edit the agent on the platform by hand. Change the file and save it.
- Before the first save, do not read `public-test-set.json`, old `results`, traces or practice customer definitions. Build blind from the brief, OpenAPI and platform docs. Practice evidence is for fixing after the first test.
- If the brief defines calendar, deadline or date-normalization rules, first extract the policy from whatever form the brief uses, including prose, tables, mails and amendments, then compile that policy into deterministic code. Do not assume a fixed precedence between date signals: follow precedence stated by the brief, and ask for clarification rather than discard conflicting components when the brief is silent. Return structured `normalized`, `valid`, `replacement` and `reason` values; do not ask the dialogue model to calculate dates.
- Validate a date before read-back and confirmation. A write-tool pre-hook may reject an invalid confirmed value, but must never silently replace it with a different date after the customer confirmed it; route back for a new read-back instead.
- When limits, attempts or dispositions apply per item, keep `attempts_allowed`, `attempts_used`, offers made and terminal disposition in deterministic per-item state. Compute the next permitted action from that state; do not ask the dialogue model to count attempts from conversation history. Once an item reaches a terminal disposition, exclude it from later offers.
- When a tool call fails, the agent must say so. It must never tell the customer something is recorded when it is not.
- A customer that passes once can fail the next time. Prefer rules in routes and code over rules in prompts.
- When a read returns several items, verify that every item reaches exactly one disposition allowed by the brief before the call closes, unless a transfer replaces item-level writes.
