"use client";

import Link from "next/link";
import { PORT_OPTIONS, scopeHref, type PortId, type ViewScope } from "@/lib/view-state";

export default function AppHeader({ page, scope, onPortChange, ports = PORT_OPTIONS }: {
  page: "map" | "pipeline";
  scope: ViewScope;
  onPortChange: (port: PortId) => void;
  ports?: readonly { port_id: string; name: string }[];
}) {
  const available = PORT_OPTIONS.filter(item => ports.some(port => port.port_id === item.port_id));
  const selectedAvailable = available.some(item => item.port_id === scope.port);
  return <header className="app-header">
    <Link className="brand" href={scopeHref("/", scope)} prefetch={false} aria-label="PortWatch home">
      <span className="brand-symbol" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 18 18 6M7 6h11v11" />
        </svg>
      </span>
      PortWatch
    </Link>
    <nav className="primary-navigation" aria-label="Primary navigation">
      <Link href={scopeHref("/", scope)} prefetch={false} aria-current={page === "map" ? "page" : undefined}>Vessel map</Link>
      <Link href={scopeHref("/pipeline", scope)} prefetch={false} aria-current={page === "pipeline" ? "page" : undefined}>Data pipeline</Link>
    </nav>
    <label className="port-picker" htmlFor="selected-port">
      Port
      <select id="selected-port" aria-label="Select port" value={scope.port} onChange={event => {
        const next = event.target.value;
        if (next !== scope.port && (next === "los_angeles" || next === "oakland")) onPortChange(next);
      }}>
        {!selectedAvailable && <option value={scope.port} disabled>
          {scope.port === "oakland" ? "Oakland" : "Los Angeles"} (unavailable)
        </option>}
        {available.map(item => <option key={item.port_id} value={item.port_id}>{item.name}</option>)}
      </select>
    </label>
  </header>;
}
