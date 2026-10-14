"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import AppHeader from "@/components/app-header";
import PipelineDiagram from "@/components/pipeline-diagram";
import HistoryResults from "@/components/history-results";
import { startVisiblePolling } from "@/lib/visible-polling";
import { PORT_OPTIONS, portName, useViewScope } from "@/lib/view-scope";
import { scopeHref } from "@/lib/view-state";

type Series = {
  status: string;
  canonical_positions?: number;
  quarantined_records?: number;
  duplicate_records?: number;
  awaiting_processing?: number;
  last_clean_commit_utc?: string;
  observation_age_seconds?: number;
  receipt_to_analytical_latency?: { sample_batches: number; median_seconds: number | null };
};
type Report = {
  repository: string;
  stages: { name: string; description: string; path: string }[];
  ports: {
    port_id: string; name: string; checked_at_utc: string; overall: string;
    collector: { status: string; last_observed_ais_message_utc?: string };
    context: Record<string, {status: string; last_http_status?: number | null}> | null;
    historical: Series; live: Series;
    runs: { recent: { run_id: string; stage: string; status: string; started_at: string; duration_seconds: number | null; input_count: number | null; output_count: number | null }[] } | null;
    operations?: { status: string; backups: { database: string; status: string; created_at_utc?: string }[] };
  }[];
};
const words = (value: string) => value.replaceAll("_", " ");
const count = (value: number | null | undefined) => value == null ? "Unknown" : value.toLocaleString(undefined, {maximumFractionDigits: 1});
const date = (value?: string) => value ? new Date(value).toLocaleString("en-GB", { timeZone: "UTC" }) + " UTC" : "Not recorded";

export default function PipelineView() {
  const { scope, updateScope } = useViewScope();
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => startVisiblePolling({
      load: async (signal) => {
        const response = await fetch("/api/pipeline", { cache: "no-store", signal });
        if (!response.ok) throw new Error("disconnected");
        return await response.json() as Report;
      },
      onData: (data) => { setReport(data); setError(false); },
      onError: () => setError(true),
  }), [retry]);
  const availablePorts = report
    ? PORT_OPTIONS.filter(option => report.ports.some(port => port.port_id === option.port_id))
    : PORT_OPTIONS;
  const selectedPorts = report?.ports.filter(port => port.port_id === scope.port) ?? [];
  const preparedMode = report?.ports.some(port => port.overall === "prepared_snapshot") ?? false;
  const mapScope = { ...scope, mode: preparedMode ? "historical" as const : scope.mode };
  useEffect(() => {
    if (preparedMode && scope.mode === "live") updateScope({ mode: "historical" }, true);
  }, [preparedMode, scope.mode, updateScope]);

  return <main className="workspace pipeline-workspace">
    <AppHeader page="pipeline" scope={mapScope} onPortChange={port => updateScope({ port })} ports={availablePorts} />
    <div className="pipeline-content">
    <section className="pipeline-intro">
      <h1>Maritime data engineering.</h1>
      <p>Python validates source evidence. PostGIS builds port analytics; Prefect schedules processing. dbt records the frozen seven-day Los Angeles history.</p>
      <p className="panel-note">Map view: {portName(scope.port)} · {mapScope.mode === "historical" ? "Historical" : "Live"}. Operational evidence below reflects current saved measurements. Seven-day history remains a fixed Los Angeles artifact.</p>
      {preparedMode
        ? <p className="panel-note">Historical snapshot session. Current collection measurements are unavailable.</p>
        : <p className="panel-note">Refreshes every 30 seconds while visible. Live coverage depends on received AIS reports; gaps remain unknown.</p>}
    </section>
    <PipelineDiagram />
    <section className="pipeline-section" aria-labelledby="quality-heading">
      <h2 id="quality-heading">Data quality and workflow evidence</h2>
      <p>Raw batches have SHA-256 checksums. Clean records retain their source batch and record offset. Spatial assignments carry port and geofence versions; visits and hourly marts record their inference rules and workflow lineage.</p>
      <p className="panel-note">Measurements below follow the selected port. Historical replay and current collection remain separate; missing measurements are unknown.</p>
    </section>
    {error && <div role="alert" className="query-error">Pipeline status is disconnected. {report && "The last received results below are stale."} <button onClick={() => setRetry(v => v + 1)}>Retry pipeline</button></div>}
    {!report && !error && <p role="status">Reading pipeline measurements…</p>}
    {report && <>
      {!selectedPorts.length && <p role="status">Operational measurements for {portName(scope.port)} are unavailable in this session. Choose an available port above.</p>}
      {selectedPorts.map(port => <section className="pipeline-section" key={port.port_id} aria-label={`${port.name} pipeline`}>
        <div className="panel-heading"><h2>{port.name}</h2><span>{words(port.overall)}</span></div>
        <p>Collector: <strong>{words(port.collector.status)}</strong> · Last AIS report received: {date(port.collector.last_observed_ais_message_utc)}</p>
        <p className="panel-note">Context sources: {port.context ? Object.entries(port.context).map(([kind, state]) => `${words(kind)}: ${words(state.status)}${state.last_http_status ? ` (HTTP ${state.last_http_status})` : ""}`).join(" · ") : "Not connected"}.</p>
        <div className="table-scroll" role="region" aria-label={`${port.name} historical and live processing measurements`} tabIndex={0}><table className="pipeline-table"><caption>Historical and live processing remain separate</caption><thead><tr><th scope="col">Measurement</th><th scope="col">Historical</th><th scope="col">Live collection</th></tr></thead>
          <tbody>
            <tr><th scope="row">Data state</th><td>{words(port.historical.status)}</td><td>{words(port.live.status)}</td></tr>
            <tr><th scope="row">Canonical positions</th><td>{count(port.historical.canonical_positions)}</td><td>{count(port.live.canonical_positions)}</td></tr>
            <tr><th scope="row">Duplicate records</th><td>{count(port.historical.duplicate_records)}</td><td>{count(port.live.duplicate_records)}</td></tr>
            <tr><th scope="row">Quarantined records</th><td>{count(port.historical.quarantined_records)}</td><td>{count(port.live.quarantined_records)}</td></tr>
            <tr><th scope="row">Batches awaiting processing</th><td>{count(port.historical.awaiting_processing)}</td><td>{count(port.live.awaiting_processing)}</td></tr>
            <tr><th scope="row">Last clean commit</th><td>{date(port.historical.last_clean_commit_utc)}</td><td>{date(port.live.last_clean_commit_utc)}</td></tr>
            <tr><th scope="row">Median receipt → clean commit</th><td>{count(port.historical.receipt_to_analytical_latency?.median_seconds)} s</td><td>{count(port.live.receipt_to_analytical_latency?.median_seconds)} s</td></tr>
          </tbody></table></div>
        <p className="panel-note">Latency uses recorded raw receipt and clean commit times, including delayed or manual processing. It does not measure source-to-screen latency or guarantee coverage.</p>
        <h3>Recent workflow runs</h3>
        {port.runs?.recent.length ? <div className="table-scroll" role="region" aria-label={`${port.name} recent workflow runs`} tabIndex={0}><table className="pipeline-table"><thead><tr><th scope="col">Workflow</th><th scope="col">State</th><th scope="col">Started</th><th scope="col">Duration</th><th scope="col">Input / output</th></tr></thead><tbody>
          {port.runs.recent.slice(0, 6).map(run => <tr key={run.run_id}><td>{words(run.stage)}</td><td>{words(run.status)}</td><td>{date(run.started_at)}</td><td>{count(run.duration_seconds)} s</td><td>{count(run.input_count)} / {count(run.output_count)}</td></tr>)}
        </tbody></table></div> : <p>No workflow history is connected for this port.</p>}
        <p className="panel-note">Counts belong to each workflow’s stage and may use different units. Checked {date(port.checked_at_utc)}.</p>
      </section>)}
    </>}
    <HistoryResults />
    <section className="pipeline-section" aria-labelledby="operations-heading">
      <h2 id="operations-heading">Operational limits and recovery</h2>
      <p>Vessel properties and destinations are reported AIS data. Waiting candidates and visits are experimental inferences, and approximate zones are not official port boundaries.</p>
      {selectedPorts.map(port => port.operations?.backups.length ? <p className="panel-note" key={port.port_id}>Database backups: {port.operations.backups.map(backup => `${backup.database}: ${words(backup.status)}${backup.created_at_utc ? ` (${date(backup.created_at_utc)})` : ""}`).join(" · ")}. Two compressed copies are kept on the second Railway volume.</p> : null)}
      <p className="panel-note">The continuous collectors run independently of page visits. Current source health and saved backups do not guarantee complete coverage or protection from loss of the hosting provider.</p>
    </section>
    <footer className="app-footer">PortWatch · Public read-only pipeline view · <Link href={scopeHref("/", mapScope)} prefetch={false}>Return to the vessel map</Link></footer>
    </div>
  </main>;
}
