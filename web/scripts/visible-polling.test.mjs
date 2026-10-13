import assert from "node:assert/strict";
import test from "node:test";
import { startVisiblePolling } from "../src/lib/visible-polling.ts";

class FakeDocument extends EventTarget {
  visibilityState;
  visibilityListeners = new Set();

  constructor(visibilityState) {
    super();
    this.visibilityState = visibilityState;
  }

  addEventListener(type, listener, options) {
    if (type === "visibilitychange") this.visibilityListeners.add(listener);
    super.addEventListener(type, listener, options);
  }

  removeEventListener(type, listener, options) {
    if (type === "visibilitychange") this.visibilityListeners.delete(listener);
    super.removeEventListener(type, listener, options);
  }

  setVisibility(state) {
    this.visibilityState = state;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

class FakeWindow {
  now = 0;
  nextId = 1;
  intervals = new Map();

  setInterval(callback, intervalMs) {
    const id = this.nextId++;
    this.intervals.set(id, {
      callback,
      intervalMs,
      dueAt: this.now + intervalMs,
    });
    return id;
  }

  clearInterval(id) {
    this.intervals.delete(id);
  }

  advance(milliseconds) {
    const end = this.now + milliseconds;
    for (;;) {
      const next = [...this.intervals.values()]
        .filter((interval) => interval.dueAt <= end)
        .sort((left, right) => left.dueAt - right.dueAt)[0];
      if (!next) break;
      this.now = next.dueAt;
      next.dueAt += next.intervalMs;
      next.callback();
    }
    this.now = end;
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function settlePromises() {
  await Promise.resolve();
  await Promise.resolve();
}

function setup(context, visibilityState = "visible", intervalMs = 100) {
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const document = new FakeDocument(visibilityState);
  const window = new FakeWindow();
  Object.defineProperty(globalThis, "document", { configurable: true, value: document });
  Object.defineProperty(globalThis, "window", { configurable: true, value: window });
  const requests = [];
  const data = [];
  const errors = [];
  let starts = 0;
  const stop = startVisiblePolling({
    intervalMs,
    load: (signal) => {
      const request = { ...deferred(), signal };
      requests.push(request);
      return request.promise;
    },
    onStart: () => { starts += 1; },
    onData: (value) => data.push(value),
    onError: (error) => errors.push(error),
  });
  context.after(() => {
    stop();
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete globalThis.document;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else delete globalThis.window;
  });
  return { document, window, requests, data, errors, stop, get starts() { return starts; } };
}

test("a hidden initial tab stays idle until it becomes visible", async (context) => {
  const state = setup(context, "hidden");
  state.window.advance(10_000);
  state.document.setVisibility("hidden");
  assert.equal(state.requests.length, 0);
  assert.equal(state.starts, 0);

  state.document.setVisibility("visible");
  assert.equal(state.requests.length, 1);
  assert.equal(state.starts, 1);
  state.requests[0].resolve("current");
  await settlePromises();
  assert.deepEqual(state.data, ["current"]);
});

test("a visible tab refreshes initially and on its normal interval", async (context) => {
  const state = setup(context);
  assert.equal(state.requests.length, 1);
  state.requests[0].resolve("initial");
  await settlePromises();
  state.window.advance(99);
  assert.equal(state.requests.length, 1);
  state.window.advance(1);
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("updated");
  await settlePromises();
  assert.deepEqual(state.data, ["initial", "updated"]);
});

test("a historical one-shot load skips periodic polling and refreshes once on resume", async (context) => {
  const state = setup(context, "visible", null);
  assert.equal(state.requests.length, 1);
  state.requests[0].resolve("historical");
  await settlePromises();
  state.window.advance(60_000);
  assert.equal(state.requests.length, 1);
  assert.equal(state.window.intervals.size, 0);

  state.document.setVisibility("hidden");
  state.document.setVisibility("visible");
  state.document.setVisibility("visible");
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("historical refreshed");
  await settlePromises();
  state.window.advance(60_000);
  assert.equal(state.requests.length, 2);
  assert.equal(state.window.intervals.size, 0);
  assert.deepEqual(state.data, ["historical", "historical refreshed"]);
});

test("hiding aborts an in-flight load and keeps the tab quiet", async (context) => {
  const state = setup(context);
  state.document.setVisibility("hidden");
  assert.equal(state.requests[0].signal.aborted, true);
  state.window.advance(10_000);
  state.requests[0].reject(new Error("late failure"));
  await settlePromises();
  assert.equal(state.requests.length, 1);
  assert.deepEqual(state.data, []);
  assert.deepEqual(state.errors, []);
  assert.equal(state.window.intervals.size, 0);
});

test("a real resume refreshes once and duplicate visibility events do nothing", async (context) => {
  const state = setup(context);
  state.requests[0].resolve("initial");
  await settlePromises();
  state.document.setVisibility("visible");
  assert.equal(state.requests.length, 1);
  state.document.setVisibility("hidden");
  state.document.setVisibility("hidden");
  state.window.advance(1_000);
  state.document.setVisibility("visible");
  state.document.setVisibility("visible");
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("resumed");
  await settlePromises();
  state.document.setVisibility("visible");
  assert.equal(state.requests.length, 2);
  state.window.advance(100);
  assert.equal(state.requests.length, 3);
  assert.deepEqual(state.data, ["initial", "resumed"]);
});

test("slow loads never overlap or accumulate missed interval refreshes", async (context) => {
  const state = setup(context);
  state.window.advance(1_050);
  assert.equal(state.requests.length, 1);
  assert.equal(state.starts, 1);
  state.requests[0].resolve("slow");
  await settlePromises();
  assert.equal(state.requests.length, 1);
  state.window.advance(50);
  assert.equal(state.requests.length, 2);
});

test("a failed visible load reports its error and later polling recovers", async (context) => {
  const state = setup(context);
  const error = new Error("temporary failure");
  state.requests[0].reject(error);
  await settlePromises();
  assert.deepEqual(state.errors, [error]);
  state.window.advance(100);
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("recovered");
  await settlePromises();
  assert.deepEqual(state.data, ["recovered"]);
});

test("resume waits for an aborted loader and discards its late data", async (context) => {
  const state = setup(context);
  state.document.setVisibility("hidden");
  state.document.setVisibility("visible");
  state.document.setVisibility("visible");
  state.window.advance(1_000);
  assert.equal(state.requests.length, 1);
  state.requests[0].resolve("obsolete");
  await settlePromises();
  assert.deepEqual(state.data, []);
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("fresh");
  await settlePromises();
  assert.deepEqual(state.data, ["fresh"]);
  assert.deepEqual(state.errors, []);
  assert.equal(state.requests.length, 2);
});

test("a late aborted failure cannot replace the resumed tab's state", async (context) => {
  const state = setup(context);
  state.document.setVisibility("hidden");
  state.document.setVisibility("visible");
  state.requests[0].reject(new Error("obsolete failure"));
  await settlePromises();
  assert.deepEqual(state.errors, []);
  assert.equal(state.requests.length, 2);
  state.requests[1].resolve("fresh");
  await settlePromises();
  assert.deepEqual(state.data, ["fresh"]);
});

test("stopping cancels a queued resume and ignores a late failure", async (context) => {
  const state = setup(context);
  state.document.setVisibility("hidden");
  state.document.setVisibility("visible");
  state.stop();
  state.requests[0].reject(new Error("after unmount"));
  await settlePromises();
  state.window.advance(10_000);
  assert.equal(state.requests.length, 1);
  assert.deepEqual(state.errors, []);
  assert.equal(state.window.intervals.size, 0);
  assert.equal(state.document.visibilityListeners.size, 0);
});

test("stopping aborts work, removes timers and listeners, and discards late results", async (context) => {
  const state = setup(context);
  state.stop();
  state.stop();
  assert.equal(state.requests[0].signal.aborted, true);
  assert.equal(state.window.intervals.size, 0);
  assert.equal(state.document.visibilityListeners.size, 0);
  state.document.setVisibility("hidden");
  state.document.setVisibility("visible");
  state.window.advance(10_000);
  state.requests[0].resolve("after unmount");
  await settlePromises();
  assert.equal(state.requests.length, 1);
  assert.deepEqual(state.data, []);
  assert.deepEqual(state.errors, []);
});
