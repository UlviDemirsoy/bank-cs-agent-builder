# Final run

1. Unzip the case and read its `README.md`.
2. Generate the contract mock:
   `python3 builder/build_mock.py CASE_FOLDER`
3. Validate and deploy it:
   `cd mock && npm run typecheck && npm run deploy`
4. Confirm `GET https://WORKER_URL/health` returns `{"ok":true,...}`.
5. Run the builder once and keep its output:
   `python3 builder/build.py CASE_FOLDER https://WORKER_URL`
6. Confirm these exact artifacts exist:
   - `submission/final.workflow.json`
   - `submission/final.trace.jsonl`
7. Commit and push that unchanged run.
8. Generate submission metadata:
   `python3 builder/finalize_submission.py --team TEAM --member "Name,email" --repo REPO_URL --mock-url WORKER_URL`
9. Check `submission.json`, commit it, push, and send it before the deadline.

Keep the deployed Worker online until results are announced. Never commit API keys,
environment files, hidden fixtures, or manually edited final workflow output.
