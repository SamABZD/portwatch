"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import HarborMap from "./harbor-map";
import NewsPanel from "./news-panel";
import AppHeader from "./app-header";
import Link from "next/link";
import { useViewScope } from "@/lib/view-scope";
import type { Dataset, Frame, Health, Mode, Vessel } from "@/lib/types";
import { startVisiblePolling } from "@/lib/visible-polling";

function labelForZone(zone: string) { return zone === "UTC" ? "UTC" : "Pacific"; }
function coordinate(value: number | null) { return value == null ? "Unknown" : value.toFixed(5); }
function text(value: string) {
  return value.replaceAll("_", " ");
}
function latestTime(...values: (string | null | undefined)[]) {
  return values
    .filter((value): value is string => !!value)
    .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
}
function number(value: number | null | undefined) {
  return value == null
    ? "Unknown"
    : value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
function age(seconds: number | null | undefined) {
  if (seconds == null) return "Unknown";
  return seconds >= 86400
    ? `${number(seconds / 86400)} days`
    : seconds >= 3600
      ? `${number(seconds / 3600)} hours`
      : `${number(seconds / 60)} minutes`;
}
function time(value: string | null | undefined, zone: string, date = false) {
  if (!value) return "Unavailable";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    ...(date ? { month: "short", day: "numeric" } : {}),
  }).format(new Date(value));
}
function vesselType(code: number | null) {
  if (code == null || code === 0) return "Unknown type";
  if (code >= 70 && code <= 79) return "Cargo";
  if (code >= 80 && code <= 89) return "Tanker";
  if (code >= 60 && code <= 69) return "Passenger";
  return (
    (
      {
        30: "Fishing",
        50: "Pilot",
        52: "Tug",
        36: "Sailing",
        37: "Pleasure craft",
      } as Record<number, string>
    )[code] || `AIS type ${code}`
  );
}
async function query<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/${path}`, { signal, cache: "no-store" });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.detail?.code || "prepared_data_unavailable");
  return data;
}

export default function Dashboard() {
  const { scope, updateScope, href } = useViewScope();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const port = scope.port;
  const [ports, setPorts] = useState<{ port_id: string; name: string }[]>([]);
  const [preparedMode, setPreparedMode] = useState(false);
  const mode = preparedMode ? "historical" : scope.mode;
  const minute = scope.minute;
  const [zone, setZone] = useState("UTC");
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [staleness, setStaleness] = useState(15);
  const [showStale, setShowStale] = useState(false);
  const [overlays, setOverlays] = useState(true);
  const [trails, setTrails] = useState(true);
  const [basemap, setBasemap] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [frame, setFrame] = useState<Frame | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [tick, setTick] = useState(0);
  const [healthError, setHealthError] = useState(false);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const dataset = datasets.find((d) => d.port_id === port && d.mode === mode && d.available);
  const cutoff = dataset
    ? new Date(
        new Date(dataset.start_utc).getTime() + minute * 60000,
      ).toISOString()
    : "";
  const shown =
    frame &&
    frame.dataset.mode === mode &&
    frame.dataset.port_id === port &&
    (mode === "live" ||
      new Date(frame.cutoff_utc).getTime() === new Date(cutoff).getTime()) &&
    frame.staleness_minutes === staleness
      ? frame
      : null;
  const vessel = shown?.vessels.find((v) => v.mmsi === selected) || null;

  const loadCatalog = useCallback(() => {
    setCatalogRetry((value) => value + 1);
  }, []);
  useEffect(() => {
    const stop = startVisiblePolling({
      intervalMs: null,
      onStart: () => { setCatalogError(null); setCatalogLoading(true); },
      load: (signal) => query<{ datasets: Dataset[]; ports: { port_id: string; name: string }[]; prepared_mode: boolean }>(`catalog?port=${port}`, signal),
      onData: (data) => {
        setCatalogLoading(false);
        setDatasets(data.datasets); setPorts(data.ports); setPreparedMode(data.prepared_mode);
        if (data.prepared_mode) {
          const first = data.ports[0]?.port_id;
          updateScope({
            mode: "historical",
            port: !data.ports.some(item => item.port_id === port)
              && (first === "los_angeles" || first === "oakland") ? first : port,
          }, true);
        }
        // A successful catalog is one-shot; revision changes and retries reload it.
        stop();
      },
      onError: (failure) => {
        setCatalogLoading(false);
        setCatalogError(failure instanceof Error ? failure.message : "prepared_data_unavailable");
        setBusy(false);
      },
    });
    return stop;
  }, [port, catalogRetry, updateScope]);
  useEffect(() => {
    setPlaying(false); setSelected(null); setFrame(null); setHealth(null);
    setSearch(""); setError(null);
  }, [port]);
  useEffect(() => {
    setPlaying(false); setSelected(null); setError(null);
    if (preparedMode && scope.mode === "live") updateScope({ mode: "historical" }, true);
  }, [mode, preparedMode, scope.mode, updateScope]);
  useEffect(() => {
    if (!dataset) {
      setFrame(null);
      setBusy(false);
      return;
    }
    const params = new URLSearchParams({
      mode,
      port,
      stale: String(staleness),
      trail: "30",
    });
    if (mode === "historical") params.set("at", cutoff);
    return startVisiblePolling({
      intervalMs: mode === "live" ? 30000 : null,
      onStart: () => { setBusy(true); setError(null); },
      load: (signal) => query<Frame>(`frame?${params}`, signal),
      onData: (result) => {
        setFrame(result);
        setBusy(false);
        if (result.dataset.revision !== dataset.revision) loadCatalog();
      },
      onError: (failure) => {
        setError(failure instanceof Error ? failure.message : "prepared_data_unavailable");
        setBusy(false);
        setPlaying(false);
      },
    });
  }, [mode, port, cutoff, dataset?.revision, staleness, tick, loadCatalog]);
  useEffect(() => startVisiblePolling({
        load: (signal) => query<Health>(`health?port=${port}`, signal),
        onData: (result) => {
          setHealth(result);
          setHealthError(false);
        },
        onError: () => setHealthError(true),
  }), [port]);
  useEffect(() => {
    if (!playing || mode !== "historical") return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible" && !busyRef.current)
        updateScope(current => ({ minute: Math.min(1440, current.minute + 5 * speed) }), true);
    }, 1200);
    return () => clearInterval(timer);
  }, [playing, speed, mode, updateScope]);
  useEffect(() => {
    if (minute >= 1440) setPlaying(false);
  }, [minute]);
  function switchMode(next: Mode) {
    if (next === mode || (next === "live" && preparedMode)) return;
    setPlaying(false);
    setSelected(null);
    setFrame(null);
    setError(null);
    updateScope({ mode: next });
  }
  const labelZone = zone === "UTC" ? "UTC" : "Pacific";
  const list = (shown?.vessels || []).filter((v) => `${v.mmsi} ${v.reported_profile?.name || ""}`.toLowerCase().includes(search.toLowerCase()));
  const plotted = shown?.vessels.filter(v =>
    typeof v.longitude === "number" && Number.isFinite(v.longitude)
    && typeof v.latitude === "number" && Number.isFinite(v.latitude)
    && (showStale || !v.stale)).length;

  return (
    <main className="workspace">
      <AppHeader page="map" scope={{ ...scope, mode }} onPortChange={next => updateScope({ port: next })}
        ports={ports.length ? ports : undefined} />

      <section className="intro-row" aria-label="Dataset selection">
        <div>
          <h1>PortWatch — maritime data engineering demo</h1>
          <p className="purpose-description">Trace vessel reports from ingestion and validation to PostGIS analytics, historical replay and the map.</p>
          <p className="purpose-stack">Python · PostGIS · Prefect · dbt for the frozen Los Angeles history</p>
          <Link className="purpose-link" href={href("/pipeline")} prefetch={false}>Explore the data pipeline</Link>
        </div>
        <div className="mode-controls">
          <span className="control-label">Data mode</span>
          <div className="segmented" role="group" aria-label="Data mode">
            <button aria-pressed={mode === "historical"} onClick={() => switchMode("historical")}>Historical replay</button>
            <button aria-pressed={mode === "live"} disabled={preparedMode}
              aria-describedby={preparedMode ? "prepared-mode-note" : undefined}
              onClick={() => switchMode("live")}>Live</button>
          </div>
          <p>
            {mode === "historical"
              ? "Jan 1 → Jan 2, 2024 UTC · saved NOAA OCM AIS · event-time replay"
              : "Live AISStream collection · received positions refresh every 30 seconds"}
          </p>
        </div>
      </section>

      {preparedMode && <p className="panel-note" id="prepared-mode-note">Historical snapshot session. Live collection is unavailable.</p>}

      {catalogError && (
        <div className="unavailable" role="alert">
          <strong>
            {catalogError === "query_api_disconnected"
              ? "The query API is disconnected."
              : "Prepared data is unavailable."}
          </strong>
          <p>
            {catalogError === "query_api_disconnected"
              ? "Start the local query API, then retry. Saved data is preserved."
              : "Check the database and saved dataset preparation, then retry. Rendering does not collect new data."}
          </p>
          <button onClick={loadCatalog}>Retry connection</button>
        </div>
      )}

      {!catalogError && !catalogLoading && !dataset && (
        <div className="unavailable" role="status">
          <strong>{datasets.length ? "This source is unavailable." : "No datasets are available for this port."}</strong>
          <p>
            {text(
              datasets.find((d) => d.port_id === port && d.mode === mode)?.reason ||
                "No prepared data is available",
            )}
            . {mode === "live" && datasets.some(d => d.port_id === port && d.mode === "historical" && d.available)
              ? "Switch to historical replay to explore the saved day."
              : "Retry the saved dataset catalog. Collection runs independently."}
          </p>
          <button onClick={loadCatalog}>Retry datasets</button>
        </div>
      )}

      {dataset && (
        <>
          <section
            className="metric-strip"
            aria-label="Replay metrics"
            aria-busy={busy}
          >
            <Metric
              title="Fresh reporting vessels"
              value={shown?.metrics.fresh_vessels}
              note={`${staleness}-minute observation cutoff`}
            />
            <Metric
              title="Fresh harbor + terminal"
              value={shown?.metrics.fresh_port_occupancy}
              note="Approximate zones · distinct vessels"
            />
            <Metric
              title="Qualified waiting candidates"
              value={shown?.metrics.candidate_waiting}
              note="Observed low-speed proxy, not a queue"
            />
            <div className="metric">
              <span>Completed-visit dwell</span>
              <strong>
                {shown?.metrics.completed_dwell_median_seconds == null
                  ? "—"
                  : `${number(shown.metrics.completed_dwell_median_seconds / 60)} min`}
              </strong>
              <small>
                Median midpoint · n=
                {shown?.metrics.completed_dwell_sample_count ?? "—"} · prefix
                only
              </small>
            </div>
          </section>

          <section className="map-workspace" aria-label="Harbor workspace">
            <div className="map-column">
              <div className="map-toolbar">
                <span className="toolbar-label">Observed positions</span>
                <div className="map-options">
                  <Toggle
                    label="Zones"
                    value={overlays}
                    onChange={setOverlays}
                  />
                  <Toggle label="Trails" value={trails} onChange={setTrails} />
                  <Toggle
                    label="Stale"
                    value={showStale}
                    onChange={setShowStale}
                  />
                  <Toggle
                    label="Basemap"
                    value={basemap}
                    onChange={setBasemap}
                  />
                </div>
              </div>
              <HarborMap
                dataset={dataset}
                frame={shown}
                selected={selected}
                onSelect={setSelected}
                overlays={overlays}
                trails={trails}
                stale={showStale}
                basemap={basemap}
              />
              {busy && (
                <div className="updating" role="status">
                  Updating to the selected cutoff…
                </div>
              )}
              {error && (
                <div className="query-error" role="alert">
                  {text(error)}.{" "}
                  <button onClick={() => setTick((value) => value + 1)}>
                    Retry frame
                  </button>
                </div>
              )}
            </div>

            <aside className="vessel-inspector" aria-label="Vessel inspector">
              <div className="panel-heading">
                <h2>Vessel inspector</h2>
                <span>{shown?.metrics.seen_vessels ?? "—"} total observed · {plotted ?? "—"} plotted</span>
              </div>
              <p className="inspector-scope">{showStale ? "Map includes fresh and stale reports." : "Map shows fresh reports; list includes saved stale reports."}</p>
              {vessel ? (
                <VesselDetails
                  vessel={vessel}
                  zone={zone}
                  dataset={dataset}
                  onClear={() => setSelected(null)}
                />
              ) : (
                <div className="inspector-hint">
                  <span className="hint-target" aria-hidden="true">
                    ⊙
                  </span>
                  <strong>Select an observation</strong>
                  <p>
                    Choose a point on the map or a vessel below to inspect its
                    last actual report.
                  </p>
                </div>
              )}
              <label className="search-label">
                Find a vessel
                <input
                  type="search"
                  aria-label="Search MMSI"
                  placeholder="Search name or MMSI"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <div className="vessel-list" aria-label="Observed vessel list">
                {list.slice(0, 100).map((v) => (
                  <button
                    key={v.mmsi}
                    className={selected === v.mmsi ? "selected-vessel" : ""}
                    aria-label={`Select vessel ${v.mmsi}`}
                    aria-pressed={selected === v.mmsi}
                    onClick={() => setSelected(v.mmsi)}
                  >
                    <span>
                      <i
                        className={`legend-point ${v.stale ? "stale-dot" : ""}`}
                      />
                      {v.reported_profile?.name || v.mmsi}
                      <small>{vesselType(v.vessel_type ?? v.reported_profile?.reported_type ?? null)}</small>
                    </span>
                    <span>
                      {v.stale ? "Stale" : `${number(v.speed_knots)} kn`}
                    </span>
                  </button>
                ))}
                {shown && list.length === 0 && (
                  <p className="empty-list">
                    {search
                      ? "No matching identifier in this prefix."
                      : "No vessel reports at this cutoff."}
                  </p>
                )}
                {list.length > 100 && (
                  <p className="list-limit">
                    Showing first 100. Search any observed name or MMSI.
                  </p>
                )}
              </div>
            </aside>
          </section>

          <section className="replay-rail" aria-label="Replay controls">
            <div className="replay-top">
              <div className="replay-time">
                <span>
                  {mode === "historical" ? "Replay cutoff" : "Display cutoff"}
                </span>
                <strong data-testid="cutoff-clock">
                  {time(
                    mode === "historical" ? cutoff : shown?.cutoff_utc,
                    zone,
                    true,
                  )}{" "}
                  <small>{labelZone}</small>
                </strong>
              </div>
              <div className="replay-buttons">
                {mode === "historical" ? (
                  <>
                    <button
                      className="play-button"
                      aria-label={playing ? "Pause replay" : "Play replay"}
                      disabled={!!error || (busy && !playing) || minute >= 1440}
                      onClick={() => setPlaying(!playing)}
                    >
                      <span aria-hidden="true">{playing ? "Ⅱ" : "▶"}</span>
                      {playing ? "Pause" : "Play"}
                    </button>
                    <button
                      className="reset-button"
                      onClick={() => {
                        setPlaying(false);
                        updateScope({ minute: 0 });
                      }}
                    >
                      ↺ Reset
                    </button>
                    <label>
                      Speed
                      <select
                        aria-label="Replay speed"
                        value={speed}
                        onChange={(e) => setSpeed(Number(e.target.value))}
                      >
                        <option value="1">1× · 5m / tick</option>
                        <option value="3">3× · 15m / tick</option>
                        <option value="6">6× · 30m / tick</option>
                      </select>
                    </label>
                  </>
                ) : (
                  <button onClick={() => setTick((value) => value + 1)}>
                    Refresh saved reports
                  </button>
                )}
                <label>
                  Freshness
                  <select
                    aria-label="Staleness cutoff"
                    value={staleness}
                    onChange={(e) => setStaleness(Number(e.target.value))}
                  >
                    <option value="5">5 minutes</option>
                    <option value="15">15 minutes</option>
                    <option value="30">30 minutes</option>
                    <option value="60">60 minutes</option>
                  </select>
                </label>
                <label>
                  Timezone
                  <select
                    aria-label="Displayed timezone"
                    value={zone}
                    onChange={(e) => setZone(e.target.value)}
                  >
                    <option value="UTC">UTC</option>
                    <option value={dataset.port_timezone || "America/Los_Angeles"}>Pacific</option>
                  </select>
                </label>
              </div>
            </div>
            {mode === "historical" && (
              <>
                <input
                  className="time-slider"
                  aria-label="Historical time slider"
                  type="range"
                  min="0"
                  max="1440"
                  step="5"
                  value={minute}
                  onChange={(e) => {
                    setPlaying(false);
                    updateScope({ minute: Number(e.target.value) }, true);
                  }}
                />
                <div className="time-ticks">
                  {[0, 360, 720, 1080, 1440].map((m) => (
                    <span key={m}>
                      {time(
                        new Date(
                          new Date(dataset.start_utc).getTime() + m * 60000,
                        ).toISOString(),
                        zone,
                      )}
                    </span>
                  ))}
                </div>
              </>
            )}
            <p className="replay-note">
              {mode === "historical"
                ? "Actual positions at or before the cutoff. No interpolation. Weather is retrospective reanalysis."
                : "The collector is independent of this view. Old saved reports do not establish current occupancy or zero traffic."}
            </p>
          </section>

          <section className="context-grid" aria-label="Activity and context">
            <div className="activity-panel">
              <div className="panel-heading">
                <h2>Observed activity</h2>
                <span>Distinct vessels / completed UTC hour</span>
              </div>
              <Activity frame={shown} zone={zone} />
              <p className="panel-note">
                Future hours and the partial current hour are excluded. Missing
                intervals stay unknown.
              </p>
            </div>
            <div className="weather-panel">
              <div className="panel-heading">
                <h2>Weather context</h2>
                <span>{mode === "historical" ? "ERA5" : "Forecast"}</span>
              </div>
              {shown?.weather ? (
                <>
                  <div className="weather-value">
                    {number(shown.weather.wind_speed_mps)}
                    <span>m/s wind</span>
                  </div>
                  <dl className="context-details">
                    <div>
                      <dt>Gusts</dt>
                      <dd>{number(shown.weather.gusts_mps)} m/s</dd>
                    </div>
                    <div>
                      <dt>Preceding-hour rain</dt>
                      <dd>
                        {number(shown.weather.preceding_hour_precipitation_mm)}{" "}
                        mm
                      </dd>
                    </div>
                    <div>
                      <dt>Valid hour</dt>
                      <dd>
                        {time(shown.weather.valid_time_utc, zone)} {labelZone}
                      </dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p className="context-empty">
                  No matching saved weather at this cutoff.
                </p>
              )}
              <p className="panel-note">
                <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a>
                {mode === "historical"
                  ? " \u00b7 Copernicus C3S / ERA5. Retrospective, model-derived context. Not information known on the replay date."
                  : " \u00b7 Saved model forecast. Check successful-fetch age in pipeline health."}
              </p>
            </div>
            <NewsPanel mode={mode} frame={shown} zone={zone}
              liveAvailable={!preparedMode} onLive={() => switchMode("live")} />
          </section>

          <details className="provenance">
            <summary>Source &amp; inference details</summary>
            <dl className="provenance-grid">
              <div>
                <dt>Dataset</dt>
                <dd>{dataset.dataset_id}</dd>
              </div>
              <div>
                <dt>Revision</dt>
                <dd>{shown?.dataset.revision || dataset.revision}</dd>
              </div>
              <div>
                <dt>Period (UTC)</dt>
                <dd>
                  {dataset.start_utc} → {dataset.end_utc}
                </dd>
              </div>
              <div>
                <dt>Versions</dt>
                <dd>
                  {dataset.clean_version} / {dataset.geofence_version} /{" "}
                  {dataset.analytical_version}
                </dd>
              </div>
            </dl>
            <p>
              Approximate analytical zones, observed reports rather than total
              AIS coverage. Waiting is a low-speed candidate proxy; dwell uses
              completed transition-interval midpoints. Partial episodes:{" "}
              {shown?.metrics.partial_visit_count ?? "Unknown"}. Only display
              trails are downsampled.
            </p>
          </details>
        </>
      )}

      {!dataset && !catalogError && catalogLoading && (
        <div className="initial-loading" role="status">
          <div className="loading-skeleton" />
          <p>Reading the selected port dataset…</p>
        </div>
      )}

      <details className="health-panel" id="pipeline-health">
        <summary>
          <span
            className={`status-dot ${health?.overall === "ok" ? "ok" : "warning"}`}
          />
          <strong>Pipeline health</strong>
          <span>
            {healthError ? "Disconnected" : text(health?.overall || "checking")}
          </span>
          <span className="health-summary">
            Current source health is separate from historical readiness.
          </span>
        </summary>
        {health && (
          <div className="health-content">
            <dl className="health-grid">
              <div>
                <dt>Database</dt>
                <dd>{text(health.database.status)}</dd>
              </div>
              <div>
                <dt>Collector</dt>
                <dd>{text(health.collector.status)}</dd>
              </div>
              <div>
                <dt>Historical dataset</dt>
                <dd>{text(health.historical.status)}</dd>
              </div>
              <div>
                <dt>Saved live reports</dt>
                <dd>{text(health.live.status)}</dd>
              </div>
              <div>
                <dt>Saved live data age</dt>
                <dd>{age(health.live.observation_age_seconds)}</dd>
              </div>
              <div>
                <dt>Last successful processing</dt>
                <dd>
                  {time(
                    mode === "historical"
                      ? latestTime(
                          health.historical.last_clean_commit_utc,
                          health.historical.last_mart_publication_utc,
                        )
                      : latestTime(
                          health.live.last_clean_commit_utc,
                          health.live.last_mart_publication_utc,
                        ),
                    zone,
                    true,
                  )}{" "}
                  {labelZone}
                </dd>
              </div>
              <div>
                <dt>Last genuine AIS receipt</dt>
                <dd>
                  {time(
                    health.collector.last_observed_ais_message_utc,
                    zone,
                    true,
                  )}{" "}
                  {labelZone}
                </dd>
              </div>
            </dl>
            {Object.entries(health.context || {}).map(([product, item]) => (
              <p key={product}>
                {text(product)}: <strong>{text(item.status)}</strong>
                {item.last_http_status && ` · HTTP ${item.last_http_status}`}
              </p>
            ))}
            <p className="panel-note">
              Checked {time(health.checked_at_utc, zone, true)} {labelZone}.{" "}
              {healthError &&
                "This is an older snapshot; connection refresh failed."}{" "}
              Rendering never starts ingestion or migrations.
            </p>
          </div>
        )}
      </details>
      <footer>
        PortWatch · Multi-port data engineering demonstration{" "}
        <span>
          NOAA OCM / MarineCadastre · AISStream · Approximate PortWatch zones
        </span>
      </footer>
    </main>
  );
}

function Metric({
  title,
  value,
  note,
}: {
  title: string;
  value: number | null | undefined;
  note: string;
}) {
  return (
    <div className="metric">
      <span>{title}</span>
      <strong>{value === undefined ? "—" : number(value)}</strong>
      <small>{note}</small>
    </div>
  );
}
function Toggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="map-toggle">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
function VesselDetails({
  vessel: v,
  zone,
  dataset,
  onClear,
}: {
  vessel: Vessel;
  zone: string;
  dataset: Dataset;
  onClear: () => void;
}) {
  const zoneName =
    dataset.zones.features.find((f) => f.properties.zone_id === v.zone)
      ?.properties.name || text(v.zone);
  return (
    <div className="vessel-details">
      <div className="vessel-title">
        <div>
          <span>MMSI</span>
          <strong>{v.reported_profile?.name || v.mmsi}</strong>
          {v.reported_profile?.name && <small>MMSI {v.mmsi}</small>}
          <small>{vesselType(v.vessel_type ?? v.reported_profile?.reported_type ?? null)}</small>
        </div>
        <button onClick={onClear} aria-label="Clear vessel selection">
          ×
        </button>
      </div>
      <div className={`evidence-tag ${v.stale ? "stale-tag" : ""}`}>
        {v.stale ? "Stale report" : "Fresh observation"}
        {v.ambiguous_position ? " · ambiguous location" : ""}
      </div>
      <dl className="detail-grid">
        <div>
          <dt>Speed</dt>
          <dd>{number(v.speed_knots)} kn</dd>
        </div>
        <div><dt>Course / heading</dt><dd>{number(v.course_degrees)}° / {number(v.heading_degrees)}°</dd></div>
        <div><dt>Coordinates</dt><dd>{coordinate(v.latitude)}, {coordinate(v.longitude)}</dd></div>
        <div>
          <dt>Observation age</dt>
          <dd>{number(v.observation_age_seconds / 60)} min</dd>
        </div>
        <div>
          <dt>Last actual report</dt>
          <dd>
            {time(v.last_observation_utc, zone, true)}{" "}
            {zone === "UTC" ? "UTC" : "Pacific"}
          </dd>
        </div>
        <div>
          <dt>Primary zone</dt>
          <dd>{zoneName}</dd>
        </div>
        <div>
          <dt>Current waiting</dt>
          <dd>{text(v.waiting)}</dd>
        </div>
        <div>
          <dt>Waiting history</dt>
          <dd>
            {v.waiting_history.qualified_episodes} qualified /{" "}
            {v.waiting_history.associated_episodes} associated
          </dd>
        </div>
        <div>
          <dt>Visit history</dt>
          <dd>{text(v.visit_status)}</dd>
        </div>
      </dl>
      <details className="reported-profile"><summary>Reported vessel &amp; voyage details</summary>
        <dl className="detail-grid">
          <div><dt>IMO / call sign</dt><dd>{v.reported_profile?.imo || "Unknown"} / {v.reported_profile?.call_sign || "Unknown"}</dd></div>
          <div><dt>Length / beam</dt><dd>{number(v.reported_profile?.length_m)} m / {number(v.reported_profile?.beam_m)} m</dd></div>
          <div><dt>Draught</dt><dd>{number(v.reported_profile?.draught_m)} m</dd></div>
          <div><dt>Reported destination</dt><dd>{v.reported_profile?.destination || "Not received"}</dd></div>
          <div><dt>Reported ETA</dt><dd>{v.reported_profile?.reported_eta || "Not received"}</dd></div>
          <div><dt>Properties timestamp</dt><dd>{time(v.profile_available_at_utc, zone, true)} {labelForZone(zone)}</dd></div>
        </dl>
        <p className="panel-note">Reported properties may be incomplete or outdated. A destination is not an observed port call; AIS ETA does not include a year.</p>
      </details>
      {v.partial_reasons.length > 0 && (
        <p className="partial-note">
          Partial: {v.partial_reasons.map(text).join(", ")}.
        </p>
      )}
      <p className="detail-source">
        {v.source_id}
        <br />
        {text(v.timestamp_quality)}
      </p>
    </div>
  );
}
function Activity({ frame, zone }: { frame: Frame | null; zone: string }) {
  const rows = frame?.chart || [];
  if (!rows.length)
    return (
      <div className="chart-empty">
        No completed hour is available at this cutoff.
      </div>
    );
  const ceiling = Math.max(1, ...rows.map((row) => row.observed_vessels || 0));
  return (
    <div
      className="activity-chart"
      role="img"
      aria-label={`Observed vessels in completed hours: ${rows.map((row) => `${time(row.hour_utc, zone)}: ${number(row.observed_vessels)}`).join("; ")}`}
    >
      <div className="chart-y">
        <span>{ceiling}</span>
        <span>{Math.round(ceiling / 2)}</span>
        <span>0</span>
      </div>
      <div className="chart-bars">
        {rows.map((row, index) => (
          <div
            className="chart-column"
            key={row.hour_utc}
            title={`${time(row.hour_utc, zone)}: ${number(row.observed_vessels)} observed vessels`}
          >
            <div
              className={`chart-bar ${row.observed_vessels === null ? "unknown-bar" : ""}`}
              style={{
                height:
                  row.observed_vessels === null
                    ? "100%"
                    : `${Math.max(1, (row.observed_vessels / ceiling) * 100)}%`,
              }}
            />
            {(index % 3 === 0 || index === rows.length - 1) && (
              <span>{time(row.hour_utc, zone)}</span>
            )}
          </div>
        ))}
      </div>
      <span className="sr-only">
        {rows
          .map(
            (row) =>
              `${time(row.hour_utc, zone)}: ${number(row.observed_vessels)} vessels`,
          )
          .join("; ")}
      </span>
    </div>
  );
}
