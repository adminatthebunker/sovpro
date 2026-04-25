import { config } from "../config.js";

/**
 * Shared OpenRouter client.
 *
 * Both the free-tier contradictions flow (`routes/contradictions.ts`)
 * and the paid premium-reports flow (`routes/reports.ts` +
 * `lib/reports.ts`) round-trip through this helper. The two surfaces
 * have identical needs — JSON-object response, error-mapping for
 * 401/429/timeout/non-JSON — so consolidating means there's one place
 * to fix when OpenRouter changes a status code or adds a header.
 *
 * The error mapping returns a discriminated union rather than throwing
 * because callers want to map upstream codes to specific HTTP
 * responses (503 vs 429 vs 504 vs 502) and a thrown Error loses that
 * detail.
 */

export type OpenRouterError =
  | { kind: "auth"; status: 401 }
  | { kind: "rate_limit"; status: 429; rateHeaders: Record<string, string>; bodySnippet: string }
  | { kind: "timeout" }
  | { kind: "network"; message: string }
  | { kind: "upstream"; status: number; bodySnippet: string }
  | { kind: "non_json"; message: string }
  | { kind: "bad_shape"; rawSnippet: string };

export interface OpenRouterSuccess {
  content: string;
  model: string;
  tokensIn: number | null;
  tokensOut: number | null;
}

export type OpenRouterResult =
  | { ok: true; value: OpenRouterSuccess }
  | { ok: false; error: OpenRouterError };

interface OpenRouterChoice {
  message?: { content?: string };
}
interface OpenRouterResponse {
  choices?: OpenRouterChoice[];
  model?: string;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
}

/**
 * Single-turn JSON-object call. Forces `response_format: json_object`
 * and includes OpenRouter's `response-healing` plugin so malformed
 * model JSON is corrected upstream when possible.
 *
 * Caller passes a structured `messages` array (system + user is the
 * common case; reports pass system + alternating user payloads for
 * map-reduce). The `model` and `timeoutMs` arguments are explicit
 * because contradictions and reports use different defaults.
 */
export async function callJsonObjectModel(args: {
  model: string;
  messages: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  timeoutMs: number;
  temperature?: number;
}): Promise<OpenRouterResult> {
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), args.timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${config.openrouter.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${config.openrouter.apiKey}`,
        "HTTP-Referer": config.openrouter.siteUrl,
        "X-Title": config.openrouter.appName,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: args.model,
        messages: args.messages,
        // json_object is the broadly-supported flavour; strict json_schema
        // would be tighter but some providers hard-400 on schema strictness.
        response_format: { type: "json_object" },
        // OpenRouter's response-healing plugin validates + repairs malformed
        // JSON server-side. Free-tier models drop keys or emit trailing commas;
        // healing catches most of it. Non-streaming only — fine here.
        plugins: [{ id: "response-healing" }],
        temperature: args.temperature ?? 0.2,
      }),
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeoutHandle);
    if ((err as { name?: string }).name === "AbortError") {
      return { ok: false, error: { kind: "timeout" } };
    }
    return {
      ok: false,
      error: { kind: "network", message: (err as Error).message ?? "fetch failed" },
    };
  }
  clearTimeout(timeoutHandle);

  if (response.status === 401) {
    return { ok: false, error: { kind: "auth", status: 401 } };
  }
  if (response.status === 429) {
    const rateHeaders: Record<string, string> = {};
    for (const h of [
      "x-ratelimit-limit",
      "x-ratelimit-remaining",
      "x-ratelimit-reset",
      "retry-after",
    ]) {
      const v = response.headers.get(h);
      if (v) rateHeaders[h] = v;
    }
    const bodyText = await response.text().catch(() => "");
    return {
      ok: false,
      error: {
        kind: "rate_limit",
        status: 429,
        rateHeaders,
        bodySnippet: bodyText.slice(0, 500),
      },
    };
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    return {
      ok: false,
      error: { kind: "upstream", status: response.status, bodySnippet: body.slice(0, 500) },
    };
  }

  let parsed: OpenRouterResponse;
  try {
    parsed = (await response.json()) as OpenRouterResponse;
  } catch (err) {
    return {
      ok: false,
      error: { kind: "non_json", message: (err as Error).message ?? "non-json response" },
    };
  }

  const content = parsed.choices?.[0]?.message?.content;
  if (typeof content !== "string") {
    return {
      ok: false,
      error: {
        kind: "bad_shape",
        rawSnippet: JSON.stringify(parsed).slice(0, 500),
      },
    };
  }

  return {
    ok: true,
    value: {
      content,
      model: parsed.model ?? args.model,
      tokensIn: parsed.usage?.prompt_tokens ?? null,
      tokensOut: parsed.usage?.completion_tokens ?? null,
    },
  };
}
