import type { Dataset, Frame } from "./types";

type Snapshot = {
  format: "portwatch-static-v2";
  created_at_utc: string;
  ports: { port_id: "los_angeles" | "oakland"; name: string; dataset: Dataset }[];
  attribution: Record<string, string>;
};
let manifest: Promise<Snapshot> | undefined;
async function json<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error("snapshot_asset_unavailable");
  if (path.endsWith(".gz")) {
    if (!response.body) throw new Error("snapshot_asset_unavailable");
    return new Response(response.body.pipeThrough(new DecompressionStream("gzip"))).json();
  }
  return response.json();
}

/** Resolve only the published historical grid. No API server or live fallback. */
export async function snapshotQuery<T>(path: string, signal?: AbortSignal): Promise<T> {
  manifest ??= json<Snapshot>("/snapshot/manifest.json").catch(error => {
    manifest = undefined;
    throw error;
  });
  const saved = await manifest;
  signal?.throwIfAborted();
  if (saved.format !== "portwatch-static-v2" || !saved.ports.length)
    throw new Error("snapshot_manifest_invalid");
  const [endpoint, search] = path.split("?");
  const params = new URLSearchParams(search);
  const selected = saved.ports.find(item => item.port_id === params.get("port"));
  if (params.has("port") && !selected && (endpoint === "frame" || endpoint === "health"))
    throw new Error("port_unavailable_in_snapshot");
  const port = selected ?? saved.ports[0];
  if (endpoint === "catalog") return {
    datasets: [port.dataset],
    ports: saved.ports.map(({ port_id, name }) => ({ port_id, name })),
    prepared_mode: true,
    captured_at_utc: saved.created_at_utc,
    attribution: saved.attribution,
  } as T;
  if (endpoint === "frame") {
    if (params.get("mode") !== "historical" || params.get("trail") !== "30")
      throw new Error("snapshot_has_no_live_connection");
    const stale = Number(params.get("stale"));
    const minute = (Date.parse(params.get("at") ?? "") - Date.parse(port.dataset.start_utc)) / 60_000;
    if (![5, 15, 30, 60].includes(stale) || !Number.isInteger(minute)
      || minute < 0 || minute > 1440 || minute % 5 !== 0)
      throw new Error("snapshot_frame_outside_grid");
    const pack = await json<{
      base: Frame;
      variants: Record<string, { metrics: Frame["metrics"]; vessels: { index: number; changes: Partial<Frame["vessels"][number]> }[]; trail_stale: boolean[]; extra: Partial<Frame> }>;
    }>(`/snapshot/${port.port_id}/frames/${minute}.json.gz`, signal);
    const variant = pack.variants[String(stale)];
    if (!variant || variant.vessels.length !== pack.base.vessels.length
      || variant.vessels.some(ref => !Number.isInteger(ref.index) || !pack.base.vessels[ref.index])
      || variant.trail_stale.length !== pack.base.trails.length)
      throw new Error("snapshot_frame_invalid");
    return {
      ...pack.base, ...variant.extra, staleness_minutes: stale, metrics: variant.metrics,
      vessels: variant.vessels.map(ref => ({ ...pack.base.vessels[ref.index], ...ref.changes })),
      trails: pack.base.trails.map((trail, index) => ({ ...trail, stale: variant.trail_stale[index] })),
    } as T;
  }
  if (endpoint === "health") return json<T>(`/snapshot/${port.port_id}/health.json`, signal);
  if (endpoint === "pipeline") return json<T>("/snapshot/pipeline.json", signal);
  throw new Error("snapshot_query_unavailable");
}
