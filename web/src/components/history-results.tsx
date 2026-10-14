"use client";

import { useEffect, useState } from "react";

type Hour = {
  hour_utc: string; observation_count: number | null;
  confirmed_arrival_count: number | null; confirmed_departure_count: number | null;
  baseline_hours: number; baseline_eligible: boolean; anomaly_label: string | null;
};
type History = { start: string; end: string; hours: Hour[]; models: number; tests: number; limitations: string };
const count = (value: number | null) => value === null ? "Unknown" : value.toLocaleString();

export default function HistoryResults() {
  const [data, setData] = useState<History | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/history", { signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error("unavailable"); return response.json(); })
      .then(result => { setData(result); setFailed(false); })
      .catch(error => { if (error.name !== "AbortError") setFailed(true); });
    return () => controller.abort();
  }, [retry]);
  const days = data ? Object.groupBy(data.hours, hour => hour.hour_utc.slice(0, 10)) : {};
  const sum = (hours: Hour[], key: "observation_count" | "confirmed_arrival_count" | "confirmed_departure_count") =>
    hours.some(hour => hour[key] === null) ? null : hours.reduce((total, hour) => total + (hour[key] ?? 0), 0);
  return <section className="pipeline-section" id="history" aria-labelledby="history-title">
    <h2 id="history-title">Seven days of Los Angeles history</h2>
    <p>January 1–7, 2024 · NOAA AIS · All times UTC</p>
    {failed ? <p role="alert">History results are unavailable. <button onClick={() => setRetry(value => value + 1)}>Retry history</button></p>
      : !data ? <p role="status">Reading saved history results…</p> : <>
        <p>{data.hours.length} hourly results · {data.models} SQL models built · {data.tests} data tests passed</p>
        <div className="table-scroll" role="region" aria-label="Los Angeles seven-day historical daily totals" tabIndex={0}><table className="pipeline-table">
          <caption>Daily totals from the seven-day historical snapshot</caption>
          <thead><tr><th scope="col">Date, UTC</th><th scope="col">AIS positions</th><th scope="col">Inferred arrivals</th><th scope="col">Inferred departures</th><th scope="col">Hours available</th></tr></thead>
          <tbody>{Object.entries(days).map(([day, hours]) => hours && <tr key={day}>
            <th scope="row">{day}</th><td>{count(sum(hours, "observation_count"))}</td>
            <td>{count(sum(hours, "confirmed_arrival_count"))}</td><td>{count(sum(hours, "confirmed_departure_count"))}</td><td>{hours.length} / 24</td>
          </tr>)}</tbody>
        </table></div>
        <p className="panel-note">{data.limitations} Current history provides at most 167 preceding hours; labels require 168 preceding hours across seven days.</p>
        <p><a href="/api/history-lineage?view=static" target="_blank" rel="noreferrer">Explore SQL models and lineage</a> · <a href="/api/history" target="_blank" rel="noreferrer">View all hourly results</a></p>
      </>}
  </section>;
}
