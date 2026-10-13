import { NextRequest, NextResponse } from "next/server";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ endpoint: string }> },
) {
  const { endpoint } = await context.params;
    if (!["catalog", "frame", "health", "pipeline", "history", "history-lineage"].includes(endpoint))
    return NextResponse.json(
      { detail: { code: "unknown_query" } },
      { status: 404 },
    );
  try {
    const base = process.env.PORTWATCH_QUERY_URL || "http://127.0.0.1:8000";
    const target = new URL(`/api/${endpoint}`, base);
    target.search = request.nextUrl.search;
    const response = await fetch(target, {
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
    if (endpoint === "history-lineage" && response.ok)
      return new NextResponse(await response.text(), {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=3600" },
      });
    return NextResponse.json(await response.json(), {
      status: response.status,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { detail: { code: "query_api_disconnected" } },
      { status: 503 },
    );
  }
}
