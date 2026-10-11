import { DurableObject } from "cloudflare:workers";
import {
  customerKey,
  handleContractRequest,
  type JsonValue,
  type MockRequest,
  type MockResponse,
  type StateStore,
} from "./generated";

const MAX_BODY_BYTES = 1_048_576;

function jsonResponse(
  body: JsonValue,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return Response.json(body, {
    status,
    headers: { ...headers, "cache-control": "no-store" },
  });
}

async function normalizeRequest(request: Request): Promise<MockRequest> {
  const url = new URL(request.url);
  const length = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) {
    throw new Error("request_body_too_large");
  }

  let body: JsonValue = null;
  if (!["GET", "HEAD"].includes(request.method)) {
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) {
      throw new Error("request_body_too_large");
    }
    if (text) {
      try {
        body = JSON.parse(text) as JsonValue;
      } catch {
        body = text;
      }
    }
  }

  return {
    method: request.method.toUpperCase(),
    path: url.pathname,
    query: Object.fromEntries(url.searchParams),
    headers: Object.fromEntries(
      [...request.headers.entries()].map(([key, value]) => [key.toLowerCase(), value]),
    ),
    body,
  };
}

export class MockState extends DurableObject<Env> {
  async handle(serializedInput: string): Promise<string> {
    const input = JSON.parse(serializedInput) as MockRequest;
    const state: StateStore = {
      get: (key) => this.ctx.storage.get<JsonValue>(key),
      put: (key, value) => this.ctx.storage.put(key, value),
      delete: (key) => this.ctx.storage.delete(key),
    };
    const result = await handleContractRequest(input, state);
    return JSON.stringify(result);
  }
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({ ok: true, service: "freya-final-bank-mock" });
    }

    try {
      const input = await normalizeRequest(request);
      const key = customerKey(input);
      const serializedResult = await env.MOCK_STATE
        .getByName(key)
        .handle(JSON.stringify(input));
      const result = JSON.parse(serializedResult) as MockResponse;
      console.log(JSON.stringify({
        message: "mock_request",
        method: input.method,
        path: input.path,
        customer_key: key,
        status: result.status,
      }));
      return jsonResponse(result.body, result.status, result.headers);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown_error";
      const status = message === "request_body_too_large" ? 413 : 500;
      console.error(JSON.stringify({
        message: "mock_request_failed",
        path: url.pathname,
        error: message,
      }));
      return jsonResponse({ error: message }, status);
    }
  },
} satisfies ExportedHandler<Env>;
