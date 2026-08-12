import { useEffect, useRef, useState } from "react";
import { apiGet } from "./api";

// Fetches `path` and tracks loading/error/data state. Re-fetches whenever
// `path` changes (e.g. filters change). `enabled=false` skips the fetch
// entirely (data stays null, loading stays false) -- used where a filter
// combination is not yet valid.
export function useApi(path, { enabled = true } = {}) {
  const [state, setState] = useState({ data: null, loading: enabled, error: null });
  const requestId = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    const myId = ++requestId.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    apiGet(path)
      .then((data) => {
        if (myId === requestId.current) setState({ data, loading: false, error: null });
      })
      .catch((error) => {
        if (myId === requestId.current) setState({ data: null, loading: false, error });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return state;
}
