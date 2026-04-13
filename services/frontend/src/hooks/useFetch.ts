import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../api";

interface State<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

const cache = new Map<string, unknown>();

export function useFetch<T>(path: string | null, deps: unknown[] = []): State<T> & { refresh: () => void } {
  const [state, setState] = useState<State<T>>(() => ({
    data: path && cache.has(path) ? (cache.get(path) as T) : null,
    error: null,
    loading: !!path && !cache.has(path),
  }));
  const counter = useRef(0);

  useEffect(() => {
    if (!path) return;
    const key = path;
    const id = ++counter.current;

    if (cache.has(key)) {
      setState({ data: cache.get(key) as T, error: null, loading: false });
    } else {
      setState(s => ({ ...s, loading: true }));
    }

    fetchJson<T>(key)
      .then(data => {
        if (id !== counter.current) return;
        cache.set(key, data);
        setState({ data, error: null, loading: false });
      })
      .catch((err: Error) => {
        if (id !== counter.current) return;
        setState({ data: null, error: err, loading: false });
      });

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  return {
    ...state,
    refresh() {
      if (path) {
        cache.delete(path);
        counter.current++;
        setState({ data: null, error: null, loading: true });
        fetchJson<T>(path).then(d => {
          cache.set(path, d);
          setState({ data: d, error: null, loading: false });
        }).catch((err: Error) => setState({ data: null, error: err, loading: false }));
      }
    },
  };
}
