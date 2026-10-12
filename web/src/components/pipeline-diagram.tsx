"use client";

import { useState } from "react";

const components = [
  { id: "noaa", name: "NOAA", role: "Historical AIS", detail: "Daily GeoParquet → regional extract", kind: "file", path: "src/portwatch/historical.py", description: "A frozen real NOAA day provides reproducible historical replay. Los Angeles and Oakland share the verified national artifact, with separate regional datasets and lineage. This download is independent of the live feed." },
  { id: "ais", name: "AISStream", role: "Live vessel reports", detail: "WebSocket → independent collectors", kind: "signal", path: "src/portwatch/live.py", description: "One server-side collector per port preserves received reports continuously. The collector runs independently of Prefect and website visitors. Reception gaps remain unknown; the feed does not establish complete maritime coverage." },
  { id: "context", name: "Open-Meteo · GDELT", role: "Weather & news context", detail: "Bounded HTTP → context connectors", kind: "context", path: "src/portwatch/context.py", description: "Separate connectors preserve historical weather, current forecasts and recent news metadata. Context stays separate from vessel positions. Rate limits, stale responses and unavailable news remain explicit; recent news never enters the frozen historical replay." },
  { id: "raw", name: "Raw storage", role: "Local files", detail: "Original bytes · SHA-256 · receipts", kind: "storage", path: "src/portwatch/storage.py", description: "Immutable source artifacts and manifests preserve original evidence before transformation. The live spool supports recovery after interruption. Rejected records retain separate quarantine evidence; raw files are not rewritten to make a batch pass." },
  { id: "python", name: "Python", role: "Validate & normalize", detail: "Positions · profiles · context", kind: "code", path: "src/portwatch/cleaning.py", description: "Bounded transformations validate source contracts, identify duplicates and quarantine malformed records. Accepted positions and timed reported vessel properties retain their source-record lineage. Unknown values stay unknown." },
  { id: "postgis", name: "PostgreSQL", role: "PostGIS analytics", detail: "Zones → visits → hourly marts", kind: "database", path: "src/portwatch/marts.py", description: "Transactional tables store canonical observations and source-separated context. PostGIS assigns versioned approximate zones; Python/SQL publish experimental visits and hourly metrics. Historical and live partitions remain distinct. Replay reconstructs only evidence available at its cutoff." },
  { id: "api", name: "FastAPI", role: "Read-only query API", detail: "Bounded queries · revision cache", kind: "api", path: "src/portwatch/demo/api.py", description: "The API serves port-scoped frames and a narrow operational-health projection. Cached frames include dataset revisions and replay parameters. A checksummed prepared snapshot also supports historical replay without a database or live credentials. Page reads never start ingestion." },
  { id: "web", name: "Next.js", role: "Map & pipeline website", detail: "React · MapLibre · 30-second refresh", kind: "map", path: "web/src/components/dashboard.tsx", description: "The Next.js server proxies the query API. React renders received vessel positions, historical replay and the measurements below; MapLibre has a local geometry fallback. No database password or AIS key is sent to the browser." },
  { id: "prefect", name: "Prefect", role: "Scheduled processing", detail: "Live every minute · context hourly", kind: "workflow", path: "src/portwatch/orchestration.py", description: "Prefect schedules finite processing workflows, tracks attempts and resumes committed checkpoints. Weather and news schedules are staggered across ports to respect shared provider cooldowns. The continuous AIS collectors run separately; the local runtime supervises both collectors and the Prefect runner." },
] as const;

type Component = typeof components[number];

function Glyph({ kind }: { kind: Component["kind"] }) {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "file" && <><path d="M9 4h10l5 5v19H9zM19 4v6h5M13 15h7M13 20h7M13 25h4" /></>}
    {kind === "signal" && <><circle cx="16" cy="16" r="2" /><path d="M10 10a8.5 8.5 0 0 0 0 12M22 10a8.5 8.5 0 0 1 0 12M6 6a14 14 0 0 0 0 20M26 6a14 14 0 0 1 0 20" /></>}
    {kind === "context" && <><path d="M10 21H8a5 5 0 0 1 0-10 7 7 0 0 1 13-2 6 6 0 0 1 3 12h-2M12 25h8M14 29h4" /><path d="M12 17h8M16 13v8" /></>}
    {kind === "storage" && <><path d="M4 10h24v17H4zM4 10l4-5h16l4 5M12 16h8M4 22h24" /></>}
    {kind === "code" && <><path d="m11 9-7 7 7 7M21 9l7 7-7 7M19 5l-6 22" /></>}
    {kind === "database" && <><ellipse cx="16" cy="7" rx="11" ry="4" /><path d="M5 7v18c0 5 22 5 22 0V7M5 16c0 5 22 5 22 0" /></>}
    {kind === "api" && <><path d="M13 6H8v20h5M19 6h5v20h-5M12 16h8m-3-3 3 3-3 3" /></>}
    {kind === "map" && <><path d="m3 8 8-4 10 4 8-4v20l-8 4-10-4-8 4zM11 4v20M21 8v20" /><circle cx="16" cy="13" r="2" /></>}
    {kind === "workflow" && <><rect x="12" y="3" width="8" height="7" rx="1" /><rect x="3" y="22" width="8" height="7" rx="1" /><rect x="21" y="22" width="8" height="7" rx="1" /><path d="M16 10v6M7 22v-6h18v6" /></>}
  </svg>;
}

export default function PipelineDiagram() {
  const [selected, setSelected] = useState<Component>(components[5]);
  const node = (item: Component) => <button key={item.id} className={`architecture-node architecture-${item.id}`} aria-pressed={selected.id === item.id} aria-controls="architecture-details" onClick={() => setSelected(item)}>
    <span className="architecture-glyph"><Glyph kind={item.kind} /></span>
    <span className="architecture-node-copy"><strong>{item.name}</strong><span>{item.role}</span><small>{item.detail}</small></span>
  </button>;

  return <section className="pipeline-section architecture" aria-labelledby="flow-heading">
    <div className="architecture-heading"><div><h2 id="flow-heading">How the data travels</h2><p>Three source paths. Preserved evidence. One inspectable map.</p></div><span className="panel-note">Select a component to explore it.</span></div>
    <figure className="architecture-figure" aria-label="PortWatch data architecture">
      <div className="architecture-scheduler">{node(components[8])}<span className="architecture-scheduler-note">Schedules the finite workflows below.<br />Live collectors run independently.</span></div>
      <div className="architecture-board">
        <svg className="architecture-wires" viewBox="0 0 1200 344" preserveAspectRatio="none" fill="none" aria-hidden="true">
          <defs><marker id="data-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="m0 0 6 3-6 3z" fill="var(--teal)" /></marker><marker id="schedule-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="m0 0 6 3-6 3z" fill="var(--primary)" /></marker></defs>
          <g stroke="currentColor" strokeWidth="1.5" markerEnd="url(#data-arrow)"><path d="M174 52h16v120h11M174 172h27M174 292h16V172h11M379 172h28M584 172h28M790 172h28M995 172h28" /></g>
          <g className="architecture-schedule-wires" stroke="currentColor" strokeWidth="1.5" strokeDasharray="5 5" markerEnd="url(#schedule-arrow)"><path d="M498 0v82M704 0v82" /></g>
        </svg>
        <div className="architecture-sources" aria-label="Data sources">{components.slice(0, 3).map(node)}</div>
        {components.slice(3, 8).map(node)}
      </div>
      <figcaption><span className="architecture-key"><i />Saved data</span><span className="architecture-key"><i className="dashed" />Scheduled work</span><span>Historical and live datasets remain separate.</span></figcaption>
    </figure>
    <div id="architecture-details" className="architecture-details" role="region" aria-label="Component details" aria-live="polite" aria-atomic="true">
      <div><h3>{selected.name} · {selected.role}</h3><p>{selected.description}</p></div><a href={`https://github.com/SamABZD/portwatch/blob/main/${selected.path}`} target="_blank" rel="noreferrer">Read the {selected.name} implementation</a>
    </div>
  </section>;
}
