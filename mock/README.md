# Final bank mock

This Worker is deliberately a scaffold until the final service contract arrives.
`src/generated.ts` returns HTTP 503 so an ungenerated mock cannot look healthy by
accident.

From the repository root:

```sh
python3 builder/build_mock.py CASE_FOLDER
cd mock
npm run typecheck
npm run deploy:dry
npm run deploy
```

The generator recursively reads the final service contract and visible test
customers, then implements the endpoints and initial state in `src/generated.ts`.
Per-customer mutations are persisted in a Durable Object. After deployment, verify
`GET /health` and at least one contract endpoint before starting the phone-agent
builder.
