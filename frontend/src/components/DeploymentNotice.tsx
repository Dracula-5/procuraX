import { useEffect, useState } from "react";
import { useMeta } from "../lib/meta";

/** Public-demo notice, plus a wake-up message when the first API response is slow. */
export function DeploymentNotice() {
  const meta = useMeta();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!meta.isPending) return;
    const timer = window.setTimeout(() => setSlow(true), 3000);
    return () => window.clearTimeout(timer);
  }, [meta.isPending]);

  if (meta.isPending && slow) {
    return <div role="status" className="bg-surface-2 px-4 py-2 text-center text-sm text-muted">Waking up the free-tier API. The first request after a quiet period can take up to a minute.</div>;
  }
  if (meta.isError) {
    return <div role="alert" className="bg-surface-2 px-4 py-2 text-center text-sm text-danger">The API is not reachable right now. Please try again in a minute.</div>;
  }
  if (meta.data?.public_demo) {
    return <div className="bg-surface-2 px-4 py-2 text-center text-sm text-muted">Public demo: fictional company, people and data, reset nightly. Please do not enter real personal or company information.</div>;
  }
  return null;
}
