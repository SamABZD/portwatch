import assert from "node:assert/strict";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { snapshotQuery } from "../src/lib/snapshot-query.ts";

test("static queries use only the saved grid and never fall back to a server", async () => {
  const original = globalThis.fetch;
  const paths = [];
  globalThis.fetch = async path => {
    paths.push(path);
    if (path === "/snapshot/manifest.json") return new Response(JSON.stringify({
      format: "portwatch-static-v2", created_at_utc: "2026-10-23T00:00:00Z",
      ports: [{ port_id: "los_angeles", name: "Los Angeles", dataset: {
        port_id: "los_angeles", mode: "historical", start_utc: "2024-01-01T00:00:00Z",
      } }], attribution: {},
    }));
    return new Response(path.endsWith(".gz") ? gzipSync(JSON.stringify({
      base: { vessels: [{ mmsi: "synthetic", stale: false }], trails: [], staleness_minutes: 5 },
      variants: { "15": { vessels: [{ index: 0, changes: { stale: true } }], trail_stale: [], metrics: { fresh_vessels: 0 }, extra: {} } },
    })) : JSON.stringify({ saved: true }));
  };
  try {
    const catalog = await snapshotQuery("catalog?port=oakland");
    assert.equal(catalog.prepared_mode, true);
    assert.equal(catalog.ports.length, 1);
    assert.deepEqual(await snapshotQuery("frame?port=los_angeles&mode=historical&trail=30&stale=15&at=2024-01-01T12:30:00Z"), {
      vessels: [{ mmsi: "synthetic", stale: true }], trails: [], staleness_minutes: 15, metrics: { fresh_vessels: 0 },
    });
    assert.equal(paths.at(-1), "/snapshot/los_angeles/frames/750.json.gz");
    for (const query of [
      "frame?port=oakland&mode=historical&trail=30&stale=15&at=2024-01-01T12:30:00Z",
      "frame?port=los_angeles&mode=live&trail=30&stale=15",
      "frame?port=los_angeles&mode=historical&trail=30&stale=16&at=2024-01-01T12:30:00Z",
      "frame?port=los_angeles&mode=historical&trail=30&stale=15&at=2024-01-01T12:31:00Z",
      "frame?port=los_angeles&mode=historical&trail=30&stale=15&at=2024-01-03T00:00:00Z",
      "../../../private",
    ]) await assert.rejects(snapshotQuery(query));
    assert.equal(paths.length, 2);
    await snapshotQuery("health?port=los_angeles");
    assert.equal(paths.at(-1), "/snapshot/los_angeles/health.json");
  } finally { globalThis.fetch = original; }
});
