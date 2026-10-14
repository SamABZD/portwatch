"use client";

import { useCallback, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { readViewScope, scopeHref, type ViewScope } from "./view-state";

export { PORT_OPTIONS, portName } from "./view-state";
export type { PortId, ViewScope } from "./view-state";

type Change = Partial<ViewScope> | ((current: ViewScope) => Partial<ViewScope>);

export function useViewScope() {
  const params = useSearchParams();
  const scope = readViewScope(params);
  const updateScope = useCallback((change: Change, replace = false) => {
    const url = new URL(window.location.href);
    const current = readViewScope(url.searchParams);
    const next = { ...current, ...(typeof change === "function" ? change(current) : change) };
    const validated = readViewScope(new URLSearchParams({
      port: next.port, mode: next.mode, minute: String(next.minute),
    }));
    const target = scopeHref(url.pathname === "/pipeline" ? "/pipeline" : "/", validated) + url.hash;
    if (target === url.pathname + url.search + url.hash) return;
    // Next integrates native history with useSearchParams without a server navigation.
    window.history[replace ? "replaceState" : "pushState"](null, "", target);
  }, []);
  useEffect(() => {
    if ((params.has("port") && params.get("port") !== scope.port)
      || (params.has("mode") && params.get("mode") !== scope.mode)
      || (params.has("minute") && params.get("minute") !== String(scope.minute))) {
      updateScope({}, true);
    }
  }, [params, scope.port, scope.mode, scope.minute, updateScope]);
  return { scope, updateScope, href: (path: "/" | "/pipeline") => scopeHref(path, scope) };
}
