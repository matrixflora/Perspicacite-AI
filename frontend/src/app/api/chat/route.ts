// Streaming proxy for POST /api/chat → ${BACKEND}/api/chat.
//
// We needed this because the Next.js dev `rewrites` pipeline (16.x +
// Turbopack) was buffering the entire SSE response into a single
// ~64 KB chunk that only landed AFTER the backend finished. A
// 27-second wait followed by every event arriving together is exactly
// what kills "live streaming" in the browser.
//
// A Route Handler with `runtime = "nodejs"` and an explicit Web
// `ReadableStream` doesn't go through the rewrite pipeline; the
// chunks the backend sends are forwarded straight to the client.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND = process.env.PERSPICACITE_BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const body = await req.text();

  const upstream = await fetch(`${BACKEND}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body,
    // Important: keep the upstream connection alive while we forward
    // chunks. Node's fetch keeps a streaming body when the response
    // is `chunked` Transfer-Encoding, which the backend uses.
    cache: "no-store",
  });

  // Build a Response that re-emits the upstream body verbatim. We
  // explicitly set the SSE-friendly headers so neither Next nor
  // intermediate caches try to buffer this. `X-Accel-Buffering: no`
  // is the magic header for nginx / many dev proxies.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
