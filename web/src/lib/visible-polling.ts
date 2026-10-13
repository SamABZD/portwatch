type VisiblePollingOptions<T> = {
  load: (signal: AbortSignal) => Promise<T>;
  onData: (data: T) => void;
  onError: (error: unknown) => void;
  onStart?: () => void;
  intervalMs?: number | null;
};

/** Start a browser poller and return its effect cleanup function. */
export function startVisiblePolling<T>({
  load,
  onData,
  onError,
  onStart,
  intervalMs = 30_000,
}: VisiblePollingOptions<T>): () => void {
  let visible = document.visibilityState === "visible";
  let stopped = false;
  let interval: number | undefined;
  let pending: AbortController | null = null;
  let refreshAfterSettlement = false;

  const canDeliver = (controller: AbortController) =>
    !stopped && visible && !controller.signal.aborted;

  const refresh = async () => {
    if (stopped || !visible || pending) return;

    const controller = new AbortController();
    pending = controller;
    try {
      onStart?.();
      if (!canDeliver(controller)) return;
      const data = await load(controller.signal);
      if (canDeliver(controller)) onData(data);
    } catch (error) {
      if (canDeliver(controller)) onError(error);
    } finally {
      pending = null;
      if (!stopped && visible && refreshAfterSettlement) {
        refreshAfterSettlement = false;
        void refresh();
      }
    }
  };

  const startInterval = () => {
    if (intervalMs === null) return;
    interval = window.setInterval(() => void refresh(), intervalMs);
  };

  const clearInterval = () => {
    if (interval !== undefined) window.clearInterval(interval);
    interval = undefined;
  };

  const onVisibilityChange = () => {
    const nextVisible = document.visibilityState === "visible";
    if (stopped || nextVisible === visible) return;
    visible = nextVisible;

    if (!visible) {
      clearInterval();
      refreshAfterSettlement = false;
      pending?.abort();
      return;
    }

    startInterval();
    // A loader may ignore abort. Wait for it to settle before refreshing.
    if (pending) refreshAfterSettlement = true;
    else void refresh();
  };

  document.addEventListener("visibilitychange", onVisibilityChange);
  if (visible) {
    startInterval();
    void refresh();
  }

  return () => {
    stopped = true;
    clearInterval();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    refreshAfterSettlement = false;
    pending?.abort();
  };
}
