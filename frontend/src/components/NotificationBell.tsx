import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { relative } from "../lib/format";
import { useNotificationText, type NotificationItem, type NotificationPage } from "../lib/notifications";

/** Header bell: unread count, the latest notifications, and a link to the full list. Polls every 30 s. */
export function NotificationBell() {
  const { t } = useTranslation();
  const text = useNotificationText();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const recent = useQuery({
    queryKey: ["notifications", "recent"],
    queryFn: () => api.get<NotificationPage>("/notifications", { limit: 8 }),
    refetchInterval: 30_000,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["notifications"] });
  const markRead = useMutation({ mutationFn: (id: string) => api.post(`/notifications/${id}/read`), onSuccess: refresh });
  const markAll = useMutation({ mutationFn: () => api.post("/notifications/read-all"), onSuccess: refresh });

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (panel.current && !panel.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const unread = recent.data?.unread_count ?? 0;
  const openItem = (item: NotificationItem) => {
    if (!item.read_at) markRead.mutate(item.id);
    setOpen(false);
    if (item.link) navigate(item.link);
  };

  return (
    <div className="relative" ref={panel}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
        aria-label={unread ? t("notifications.bellUnread", { count: unread }) : t("notifications.bell")}
        aria-expanded={open}
      >
        <Bell className="size-5" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-danger px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 max-w-[90vw] rounded-lg border border-border bg-surface shadow-lg" role="dialog" aria-label={t("notifications.title")}>
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <p className="text-sm font-semibold">{t("notifications.title")}</p>
            {unread > 0 && (
              <button type="button" className="text-xs text-primary hover:underline" onClick={() => markAll.mutate()}>
                {t("notifications.markAllRead")}
              </button>
            )}
          </div>
          {recent.data?.items.length ? (
            <ul className="max-h-96 divide-y divide-border overflow-y-auto">
              {recent.data.items.map((item) => {
                const { title, body } = text(item);
                return (
                  <li key={item.id}>
                    <button type="button" onClick={() => openItem(item)} className={clsx("block w-full px-3 py-2 text-left hover:bg-surface-2", !item.read_at && "bg-primary-soft/40")}>
                      <span className="flex items-start justify-between gap-2">
                        <span className={clsx("text-sm", !item.read_at && "font-semibold")}>{title}</span>
                        {!item.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label={t("notifications.unread")} />}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted">{body}</span>
                      <span className="mt-0.5 block text-[11px] text-muted">{relative(item.created_at)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-sm text-muted">{t("notifications.empty")}</p>
          )}
          <Link to="/app/notifications" onClick={() => setOpen(false)} className="block border-t border-border px-3 py-2 text-center text-sm text-primary hover:bg-surface-2">
            {t("notifications.viewAll")}
          </Link>
        </div>
      )}
    </div>
  );
}
