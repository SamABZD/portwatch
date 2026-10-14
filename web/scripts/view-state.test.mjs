import assert from "node:assert/strict";
import test from "node:test";
import { readViewScope, scopeHref } from "../src/lib/view-state.ts";

test("untrusted scope cannot change the API port or create a foreign route", () => {
  const scope = readViewScope(new URLSearchParams({
    port: "javascript:alert(1)", mode: "arbitrary", minute: "Infinity",
  }));
  assert.deepEqual(scope, { port: "los_angeles", mode: "live", minute: 750 });
  assert.equal(scopeHref("/pipeline", scope), "/pipeline?port=los_angeles&mode=live&minute=750");
});

test("replay bounds accept zero/end and reject out-of-range or malformed minutes", () => {
  for (const minute of ["0", "1", "750", "1440"]) {
    assert.equal(readViewScope(new URLSearchParams({ minute })).minute, Number(minute));
  }
  for (const minute of ["", "-1", "1441", "750.5", "1e3", "NaN", " 750", "0x10"]) {
    assert.equal(readViewScope(new URLSearchParams({ minute })).minute, 750);
  }
});

test("both route links retain validated port, mode and replay cutoff", () => {
  const scope = readViewScope(new URLSearchParams("port=oakland&mode=historical&minute=600"));
  for (const path of ["/", "/pipeline"]) {
    const url = new URL(scopeHref(path, scope), "https://example.test");
    assert.equal(url.pathname, path);
    assert.deepEqual(readViewScope(url.searchParams), scope);
  }
});
