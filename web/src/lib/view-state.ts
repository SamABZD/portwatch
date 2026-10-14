import type { Mode } from "./types";

export const PORT_OPTIONS = [
  { port_id: "los_angeles", name: "Port of Los Angeles" },
  { port_id: "oakland", name: "Port of Oakland" },
] as const;
export type PortId = (typeof PORT_OPTIONS)[number]["port_id"];
export type ViewScope = { port: PortId; mode: Mode; minute: number };

export function portName(port: PortId) {
  return PORT_OPTIONS.find(item => item.port_id === port)!.name;
}

/** Only known dataset selectors can reach API queries or navigation links. */
export function readViewScope(params: Pick<URLSearchParams, "get">): ViewScope {
  const port = params.get("port");
  const minute = params.get("minute");
  return {
    port: port === "oakland" ? "oakland" : "los_angeles",
    mode: params.get("mode") === "historical" ? "historical" : "live",
    minute: minute !== null && /^\d{1,4}$/.test(minute) && Number(minute) <= 1440
      ? Number(minute) : 750,
  };
}

export function scopeHref(path: "/" | "/pipeline", scope: ViewScope) {
  return `${path}?${new URLSearchParams({
    port: scope.port, mode: scope.mode, minute: String(scope.minute),
  })}`;
}
