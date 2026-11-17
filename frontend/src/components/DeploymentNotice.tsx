import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMeta } from "../lib/meta";

/** Public-demo notice, plus a wake-up message when the first API response is slow. */
export function DeploymentNotice() {
  const { t } = useTranslation();
  const meta = useMeta();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!meta.isPending) return;
    const timer = window.setTimeout(() => setSlow(true), 3000);
    return () => window.clearTimeout(timer);
  }, [meta.isPending]);

  if (meta.isPending && slow) {
    return <div role="status" className="bg-surface-2 px-4 py-2 text-center text-sm text-muted">{t("notice.waking")}</div>;
  }
  if (meta.isError) {
    return <div role="alert" className="bg-surface-2 px-4 py-2 text-center text-sm text-danger">{t("notice.unreachable")}</div>;
  }
  if (meta.data?.public_demo) {
    return <div className="bg-surface-2 px-4 py-2 text-center text-sm text-muted">{t("notice.publicDemo")}</div>;
  }
  return null;
}
