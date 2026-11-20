import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; error: ApiError }
  | { state: "ready"; data: T };

/** Load data with cancellation on unmount or reload; errors are normalised to ApiError. */
export function useApi<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): [Loadable<T>, () => void] {
  const [value, setValue] = useState<Loadable<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    const ctrl = new AbortController();
    setValue({ state: "loading" });
    loadRef.current(ctrl.signal).then(
      (data) => !ctrl.signal.aborted && setValue({ state: "ready", data }),
      (e) => {
        if (ctrl.signal.aborted || (e as Error).name === "AbortError") return;
        setValue({ state: "error", error: e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)) });
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return [value, reload];
}
