import { useEffect, useState } from "react";

export function useData<T>(loader: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      loader()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(String(e)))
        .finally(() => alive && setLoading(false));
    tick();
    const live = import.meta.env.VITE_DATA_MODE === "live";
    const id = live ? window.setInterval(tick, 5000) : undefined;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
  }, [loader]);

  return { data, loading, error };
}
