"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";

const components = [
  { id: "noaa", name: "NOAA", role: "Historical AIS", detail: "Daily GeoParquet · regional extracts", kind: "file", path: "src/portwatch/historical.py", description: "A verified NOAA day provides reproducible map replay for Los Angeles and Oakland, with separate regional datasets and lineage. The seven-day dbt publication uses January 1–7, 2024 for Los Angeles only. Historical downloads are independent of the live feed." },
  { id: "ais", name: "AISStream", role: "Continuous collection", detail: "WebSocket · one collector per port", kind: "signal", path: "src/portwatch/live.py", description: "One server-side collector per port preserves received reports continuously. Collectors run independently of Prefect and website visitors. Reception gaps remain unknown; the feed does not establish complete maritime coverage." },
  { id: "weather", name: "Open-Meteo", role: "Weather context", detail: "Historical ERA5 · current forecast", kind: "context", path: "src/portwatch/context.py", description: "Separate weather products preserve retrospective ERA5 reanalysis for matching historical periods and current forecast context for live data. Weather remains attributed model context, not an on-site sensor or evidence that a delay was caused by weather. Missing or stale responses remain explicit." },
  { id: "news", name: "GDELT · port releases", role: "Recent news context", detail: "Seven-day window · bounded HTTP", kind: "news", path: "src/portwatch/news.py", description: "A bounded GDELT query selects recent named-port news. When the primary fails, a separately attributed official port release source can provide limited fallback coverage. Saved attempts distinguish empty, failed, deferred and stale data. Recent news never enters historical replay or the frozen dbt publication." },
  { id: "raw", name: "Raw storage", role: "Artifacts & manifests", detail: "Original bytes · SHA-256 · receipts", kind: "storage", path: "src/portwatch/storage.py", description: "Immutable source artifacts and manifests preserve original evidence before transformation, on local files or the existing hosted volume. The live spool supports recovery after interruption. Rejected records retain separate quarantine evidence; raw files are not rewritten to make a batch pass." },
  { id: "python", name: "Python", role: "Validate & deduplicate", detail: "Positions · profiles · quarantine", kind: "code", path: "src/portwatch/cleaning.py", description: "Bounded Python transformations validate source contracts, normalize reported fields, identify duplicates and quarantine malformed records. Accepted positions, timed vessel properties and separately normalized context retain source-record lineage. Unknown values stay unknown." },
  { id: "postgis", name: "PostgreSQL", role: "PostGIS analytics", detail: "Zones · visits · hourly marts", kind: "database", path: "src/portwatch/marts.py", description: "Transactional tables store canonical observations and source-separated context. PostGIS assigns versioned approximate zones; Python and SQL publish experimental visits and hourly metrics. Historical and live partitions remain distinct. The historical dbt branch reads saved Python contracts; it does not process the continuous live feed." },
  { id: "api", name: "FastAPI", role: "Read-only query API", detail: "Bounded queries · revision cache", kind: "api", path: "src/portwatch/demo/api.py", description: "The API serves port-scoped frames and a narrow operational-health projection. Cached frames include dataset revisions and replay parameters. A checksummed prepared snapshot also supports historical replay without a database or live credentials. Page reads never start ingestion." },
  { id: "web", name: "Next.js", role: "Map & replay", detail: "React · MapLibre · pipeline evidence", kind: "map", path: "web/src/components/dashboard.tsx", description: "The Next.js server proxies the read-only query API. React renders received positions, bounded one-day historical replay and current pipeline measurements; MapLibre has a local geometry fallback. Visible live views refresh every 30 seconds. No database password or AIS key is sent to the browser." },
  { id: "dbt", name: "dbt", role: "Historical models & tests", detail: "Frozen LA · January 1–7, 2024", kind: "code", path: "src/portwatch/models.py", description: "The frozen seven-day Los Angeles publication builds historical SQL models in an owned PostgreSQL schema. Models read saved Python clean, spatial, visit and weather contracts, retain source separation, compare every hourly field with the reference, and publish tests and lineage. This manual finite workflow is separate from one-day map replay and current live processing; there is no automatic dbt schedule." },
  { id: "prefect", name: "Prefect", role: "Finite processing jobs", detail: "Saved live inputs every minute · context hourly", kind: "workflow", path: "src/portwatch/orchestration.py", description: "Prefect schedules finite processing workflows, tracks attempts and resumes committed checkpoints. Weather and news schedules are staggered across ports to respect shared provider cooldowns. Continuous AIS collectors run separately; the shared runtime supervises collectors and the Prefect runner locally or on the existing hosted service. Historical backfill and dbt builds are manual finite workflows." },
] as const;

type Component = typeof components[number];
type Wire = { id: string; path: string; arrow?: boolean };
const sources = components.slice(0, 4);
const processing = components.slice(4, 9);
const dbt = components[9];
const prefect = components[10];

function Glyph({ kind }: { kind: Component["kind"] }) {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "file" && <><path d="M9 4h10l5 5v19H9zM19 4v6h5M13 15h7M13 20h7M13 25h4" /></>}
    {kind === "signal" && <><circle cx="16" cy="16" r="2" /><path d="M10 10a8.5 8.5 0 0 0 0 12M22 10a8.5 8.5 0 0 1 0 12M6 6a14 14 0 0 0 0 20M26 6a14 14 0 0 1 0 20" /></>}
    {kind === "context" && <><path d="M10 21H8a5 5 0 0 1 0-10 7 7 0 0 1 13-2 6 6 0 0 1 3 12h-2M12 25h8M14 29h4" /><path d="M12 17h8M16 13v8" /></>}
    {kind === "news" && <><path d="M6 5h21v23H6zM6 10H3v15a3 3 0 0 0 3 3M11 10h11M11 16h4v6h-4zM19 16h3M19 21h3" /></>}
    {kind === "storage" && <><path d="M4 10h24v17H4zM4 10l4-5h16l4 5M12 16h8M4 22h24" /></>}
    {kind === "code" && <><path d="m11 9-7 7 7 7M21 9l7 7-7 7M19 5l-6 22" /></>}
    {kind === "database" && <><ellipse cx="16" cy="7" rx="11" ry="4" /><path d="M5 7v18c0 5 22 5 22 0V7M5 16c0 5 22 5 22 0" /></>}
    {kind === "api" && <><path d="M13 6H8v20h5M19 6h5v20h-5M12 16h8m-3-3 3 3-3 3" /></>}
    {kind === "map" && <><path d="m3 8 8-4 10 4 8-4v20l-8 4-10-4-8 4zM11 4v20M21 8v20" /><circle cx="16" cy="13" r="2" /></>}
    {kind === "workflow" && <><rect x="12" y="3" width="8" height="7" rx="1" /><rect x="3" y="22" width="8" height="7" rx="1" /><rect x="21" y="22" width="8" height="7" rx="1" /><path d="M16 10v6M7 22v-6h18v6" /></>}
  </svg>;
}

export default function PipelineDiagram() {
  const [selected, setSelected] = useState<Component>(components[6]);
  const board = useRef<HTMLDivElement>(null);
  const marker = useId();
  const [drawing, setDrawing] = useState<{ width: number; height: number; wires: Wire[] }>({ width: 1, height: 1, wires: [] });

  useLayoutEffect(() => {
    const element = board.current;
    if (!element) return;
    const bounds = (target: Element) => {
      const parent = element.getBoundingClientRect();
      const box = target.getBoundingClientRect();
      return { left: box.left - parent.left, top: box.top - parent.top, right: box.right - parent.left, bottom: box.bottom - parent.top, x: box.left - parent.left + box.width / 2, y: box.top - parent.top + box.height / 2 };
    };
    const measure = () => {
      const nodes = new Map(Array.from(element.querySelectorAll<HTMLElement>("[data-architecture-node]")).map(target => [target.dataset.architectureNode, bounds(target)]));
      const raw = nodes.get("raw");
      const sourceGroup = element.querySelector(".architecture-sources");
      const output = element.querySelector(".architecture-history-output");
      if (!raw || !sourceGroup || !output) return;
      const compact = window.matchMedia("(max-width: 760px)").matches;
      const wires: Wire[] = [];
      if (compact) {
        const group = bounds(sourceGroup);
        wires.push({ id: "sources-raw", path: `M${group.x} ${group.bottom}V${raw.top}`, arrow: true });
      } else {
        const inputs = sources.map(source => nodes.get(source.id)).filter(input => input !== undefined);
        const join = Math.max(...inputs.map(input => input.bottom)) + 24;
        inputs.forEach((input, index) => wires.push({ id: `source-${index}`, path: `M${input.x} ${input.bottom}V${join}` }));
        wires.push({ id: "source-join", path: `M${Math.min(raw.x, ...inputs.map(input => input.x))} ${join}H${Math.max(...inputs.map(input => input.x))}` });
        wires.push({ id: "sources-raw", path: `M${raw.x} ${join}V${raw.top}`, arrow: true });
      }
      processing.slice(0, -1).forEach((item, index) => {
        const start = nodes.get(item.id);
        const end = nodes.get(processing[index + 1].id);
        if (!start || !end) return;
        wires.push({ id: `${item.id}-${processing[index + 1].id}`, path: compact ? `M${start.x} ${start.bottom}V${end.top}` : `M${start.right} ${start.y}H${end.left}`, arrow: true });
      });
      const models = nodes.get("dbt");
      const spatial = nodes.get("postgis");
      const results = bounds(output);
      if (models && spatial) {
        if (!compact) wires.push({ id: "historical-postgis-dbt", path: `M${spatial.x} ${spatial.bottom}V${models.top}`, arrow: true });
        wires.push({ id: "dbt-results", path: compact ? `M${models.x} ${models.bottom}V${results.top}` : `M${models.right} ${models.y}H${results.left}`, arrow: true });
      }
      const rect = element.getBoundingClientRect();
      const next = { width: rect.width, height: rect.height, wires };
      setDrawing(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    element.querySelectorAll("[data-architecture-node], .architecture-sources, .architecture-history-output").forEach(target => observer.observe(target));
    measure();
    return () => observer.disconnect();
  }, []);

  const node = (item: Component) => <button key={item.id} type="button" data-architecture-node={item.id} className={`architecture-node architecture-${item.id}`} aria-pressed={selected.id === item.id} aria-controls="architecture-details" onClick={() => setSelected(item)}>
    <span className="architecture-glyph"><Glyph kind={item.kind} /></span>
    <span className="architecture-node-copy"><strong>{item.name}</strong><span>{item.role}</span><small>{item.detail}</small></span>
  </button>;

  return <section className="pipeline-section architecture" aria-labelledby="flow-heading">
    <div className="architecture-heading"><div><h2 id="flow-heading">How the data travels</h2><p>Separate source paths. Preserved evidence. Read-only views.</p></div><span className="panel-note">Select a component to explore it.</span></div>
    <figure className="architecture-figure" aria-label="PortWatch data architecture">
      <div className="architecture-scheduler"><div className="architecture-scheduler-control">{node(prefect)}<svg className="architecture-schedule-link" viewBox="0 0 32 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M0 8h29" strokeDasharray="4 4" /><path d="m24 3 5 5-5 5" /></svg></div><span className="architecture-scheduler-note"><strong>Schedules finite work.</strong> Saved live inputs every minute; weather and news hourly. Historical backfill and dbt are manual.<br />Continuous AIS collectors run independently.</span></div>
      <div ref={board} className="architecture-board">
        <svg className="architecture-wires" viewBox={`0 0 ${drawing.width} ${drawing.height}`} fill="none" aria-hidden="true">
          <defs><marker id={marker} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="m0 0 6 3-6 3z" fill="var(--teal)" /></marker></defs>
          <g stroke="currentColor" strokeWidth="1.5">{drawing.wires.map(wire => <path key={wire.id} data-architecture-edge={wire.id} d={wire.path} markerEnd={wire.arrow ? `url(#${marker})` : undefined} />)}</g>
        </svg>
        <ul className="architecture-sources" aria-label="Separate data source paths">{sources.map(source => <li key={source.id}>{node(source)}</li>)}</ul>
        <ol className="architecture-processing" aria-label="Saved source evidence through processing to the website">{processing.map(item => <li key={item.id}>{node(item)}</li>)}</ol>
        <div className="architecture-history" role="group" aria-label="Historical-only branch from PostGIS">
          <p className="architecture-history-origin"><strong>Historical branch from PostGIS</strong>Frozen Los Angeles history only. One-day map replay and current live evidence stay separate.</p>
          {node(dbt)}
          <div className="architecture-history-output"><h3>Published history & lineage</h3><p>Saved model results served through the read-only API.</p><a href="#history">View frozen seven-day results</a><a href="/api/history-lineage?view=static" target="_blank" rel="noreferrer">Explore SQL models and lineage</a></div>
        </div>
      </div>
      <figcaption><span className="architecture-key"><i />Saved data dependencies</span><span className="architecture-key"><i className="dashed" />Finite scheduling, separate from collection</span><span>Every source retains its own identity.</span></figcaption>
    </figure>
    <div id="architecture-details" className="architecture-details" role="region" aria-label="Component details" aria-live="polite" aria-atomic="true">
      <div><h3>{selected.name} · {selected.role}</h3><p>{selected.description}</p></div><div className="architecture-source-link"><a href={`https://github.com/SamABZD/portwatch/blob/main/${selected.path}`} target="_blank" rel="noreferrer">Read the {selected.name} implementation</a><span>Public source on GitHub.</span></div>
    </div>
  </section>;
}
