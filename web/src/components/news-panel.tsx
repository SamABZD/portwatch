import type { Frame, Mode } from "@/lib/types";

function stamp(value: string, zone: string) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: zone, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}
function age(seconds: number | null) {
  if (seconds === null) return "Unknown";
  if (seconds < 60) return "Less than a minute ago";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`;
  return `${Math.floor(seconds / 3600)} hours ago`;
}
const states = {
  available: "Saved port news is available.",
  valid_empty: "No matching articles in the saved window.",
  fallback: "Official port releases shown as fallback coverage.",
  stale: "Last good response shown; refresh failed or is overdue.",
  deferred: "Scheduled news fetch deferred; saved dates remain unchanged.",
  unavailable: "No usable saved news response is connected.",
};

export default function NewsPanel({ mode, frame, zone, liveAvailable, onLive }: {
  mode: Mode; frame: Frame | null; zone: string; liveAvailable: boolean; onLive: () => void;
}) {
  const context = frame?.news_context;
  const status = context?.status || frame?.news_status || "unavailable";
  const rows = frame?.news || [];
  const windowHours = context?.window_hours;
  const primary = context?.primary_attempt;
  return <div className="news-panel" aria-labelledby="news-title">
    <div className="panel-heading"><h2 id="news-title">
      {mode === "historical" ? "Historical context" : "Port news & context"}
    </h2></div>
    {mode === "historical" ? <>
      <strong>Current articles are excluded.</strong>
      <p>Recent news cannot explain vessel behavior on the replay date. No historical disruption evidence is available.</p>
      {liveAvailable && <button onClick={onLive}>View current port context</button>}
    </> : <>
      <p className="panel-note">{windowHours
        ? `${windowHours === 168 ? "Last 7 days" : `Saved ${windowHours}-hour window`} · UTC`
        : "Recent port context · saved window unavailable"}</p>
      <strong>{states[status as keyof typeof states] || states.unavailable}</strong>
      {context?.status === "deferred" && context.schedule?.next_allowed_utc && <p className="panel-note">
        Next eligible scheduled fetch: {stamp(context.schedule.next_allowed_utc, "UTC")} UTC.
      </p>}
      {context?.data_status === "valid_empty" && <p>
        The successful response contained no matching dated articles. This does not establish absence of port activity or disruption.
      </p>}
      {primary?.status === "failed" && <p className="panel-note">
        GDELT primary {primary.http_status === 429 ? "rate limited (HTTP 429)"
          : primary.http_status ? `unavailable (HTTP ${primary.http_status}; ${primary.error_code?.replaceAll("_", " ")})`
          : `unavailable (${primary.error_code?.replaceAll("_", " ") || "transport failure"})`}.
        {primary.http_status === 429 && primary.next_allowed_at
          ? ` No primary retry before ${stamp(primary.next_allowed_at, "UTC")} UTC.` : ""}
      </p>}
      {!!rows.length && <ul>{rows.map(item => <li key={item.url}>
        <a href={/^https?:\/\//.test(item.url) ? item.url : "#"} target="_blank" rel="noreferrer">
          {item.title}
        </a>
        <small>{item.domain} · {item.publication_time_utc
          ? `${stamp(item.publication_time_utc, zone)} ${zone === "UTC" ? "UTC" : "Pacific"}`
          : item.publication_date
            ? `${item.publication_date} · date only (${item.publication_timezone || "source calendar"}); time unknown`
            : item.first_seen_time_utc
              ? `First seen ${stamp(item.first_seen_time_utc, zone)}; publication time unknown`
              : "Publication and first-seen times unknown"}
          {!!item.relevance_tags?.length && ` · Tags: ${item.relevance_tags.join(", ")}`}
        </small>
      </li>)}</ul>}
      {context && <>
        <p className="panel-note">
          {context.source_url ? <a href={context.source_url} target="_blank" rel="noreferrer">{context.provider}</a> : context.provider}
          {" · "}{context.coverage}
        </p>
        <p className="panel-note">Last successful fetch: {age(context.success_age_seconds)}
          {context.last_successful_fetch_utc && ` (${stamp(context.last_successful_fetch_utc, "UTC")} UTC)`}
          {context.freshness === "stale" && " · stale saved response"}.
        </p>
        {context.valid_start_utc && context.valid_end_utc && <p className="panel-note">
          Window: {stamp(context.valid_start_utc, "UTC")} through {stamp(context.valid_end_utc, "UTC")} UTC (end excluded).
        </p>}
        {!!context.quality.unknown_date_count && context.using_fallback && <p className="panel-note">
          {context.quality.unknown_date_count} undated source items excluded; date coverage is incomplete.
        </p>}
        {context.saved_row_count !== null && context.saved_row_count > rows.length && <p className="panel-note">
          Showing {rows.length} of {context.saved_row_count} saved matching articles.
        </p>}
        {windowHours !== null && windowHours !== context.configured_window_hours && <p className="panel-note">
          Saved window differs from the configured seven-day scope; awaiting a successful scheduled fetch.
        </p>}
      </>}
      <p className="panel-note">Headlines provide context, not evidence of what caused vessel behavior.</p>
    </>}
  </div>;
}
