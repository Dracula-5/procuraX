import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Input, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, relative } from "../lib/format";
import { EVENT_TYPES, useNotificationText, type NotificationPage, type NotificationPreferences } from "../lib/notifications";

type ChannelKind = "slack" | "teams";
type ChannelState = { kind: ChannelKind; configured: boolean; webhook_host: string | null; events: string[]; enabled: boolean };
type ChannelsResponse = { webhooks_available: boolean; allowed_hosts: string[]; channels: ChannelState[] };
type Delivery = { id: string; event_type: string; channel: string; status: string; attempts: number; last_error: string | null; created_at: string };

export function Notifications() {
  const { t } = useTranslation();
  const text = useNotificationText();
  const { can } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["notifications", "all"], queryFn: () => api.get<NotificationPage>("/notifications", { limit: 100 }) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["notifications"] });
  const markRead = useMutation({ mutationFn: (id: string) => api.post(`/notifications/${id}/read`), onSuccess: refresh });
  const markAll = useMutation({ mutationFn: () => api.post("/notifications/read-all"), onSuccess: refresh });

  return (
    <>
      <PageHeader
        title={t("notifications.title")}
        subtitle={t("notifications.subtitle")}
        actions={
          (list.data?.unread_count ?? 0) > 0 && (
            <Button variant="secondary" onClick={() => markAll.mutate()} loading={markAll.isPending}>
              {t("notifications.markAllRead")}
            </Button>
          )
        }
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card title={t("notifications.inbox")}>
          <ErrorNotice error={list.error} />
          {list.isLoading ? (
            <Spinner />
          ) : !list.data?.items.length ? (
            <EmptyState title={t("notifications.empty")}>{t("notifications.emptyBody")}</EmptyState>
          ) : (
            <ul className="divide-y divide-border">
              {list.data.items.map((item) => {
                const { title, body } = text(item);
                return (
                  <li key={item.id} className="flex items-start justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <p className={clsx("text-sm", !item.read_at && "font-semibold")}>
                        {item.link ? (
                          <Link to={item.link} className="hover:text-primary" onClick={() => !item.read_at && markRead.mutate(item.id)}>
                            {title}
                          </Link>
                        ) : (
                          title
                        )}
                      </p>
                      <p className="text-sm text-muted">{body}</p>
                      <p className="mt-0.5 text-xs text-muted">{dateTime(item.created_at)}</p>
                    </div>
                    {!item.read_at && (
                      <Button size="sm" variant="ghost" onClick={() => markRead.mutate(item.id)}>
                        {t("notifications.markRead")}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <div className="space-y-6">
          <PreferencesCard />
        </div>
      </div>
      {can("organization:manage") && (
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <ChannelsCard />
          <DeadLettersCard />
        </div>
      )}
    </>
  );
}

function EventChecklist({ selected, onChange, idPrefix }: { selected: string[]; onChange: (next: string[]) => void; idPrefix: string }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5">
      {EVENT_TYPES.map((event) => (
        <label key={event} className="flex items-center gap-2 text-sm" htmlFor={`${idPrefix}-${event}`}>
          <input
            id={`${idPrefix}-${event}`}
            type="checkbox"
            className="accent-[var(--primary)]"
            checked={selected.includes(event)}
            onChange={() => onChange(selected.includes(event) ? selected.filter((e) => e !== event) : [...selected, event])}
          />
          {t(`notifications.events.${event}.name`)}
        </label>
      ))}
    </div>
  );
}

function PreferencesCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ["notification-preferences"], queryFn: () => api.get<NotificationPreferences>("/notifications/preferences") });
  const save = useMutation({
    mutationFn: (body: { email_enabled: boolean; muted_events: string[] }) => api.put<NotificationPreferences>("/notifications/preferences", body),
    onSuccess: (data) => qc.setQueryData(["notification-preferences"], data),
  });
  if (prefs.isLoading) return <Spinner />;
  if (!prefs.data) return <ErrorNotice error={prefs.error} />;
  const p = prefs.data;
  // In-app is on for every event the user has not muted; the checklist shows the ones they receive.
  const receiving = EVENT_TYPES.filter((e) => !p.muted_events.includes(e));
  return (
    <Card title={t("notifications.prefs.title")}>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-0.5 accent-[var(--primary)]"
          checked={p.email_enabled}
          onChange={(e) => save.mutate({ email_enabled: e.target.checked, muted_events: p.muted_events })}
        />
        <span>
          {t("notifications.prefs.email")}
          {!p.email_available && <span className="block text-xs text-muted">{t("notifications.prefs.emailUnavailable")}</span>}
        </span>
      </label>
      <p className="mt-4 mb-2 text-sm font-medium">{t("notifications.prefs.events")}</p>
      <EventChecklist
        idPrefix="pref"
        selected={receiving}
        onChange={(next) => save.mutate({ email_enabled: p.email_enabled, muted_events: EVENT_TYPES.filter((e) => !next.includes(e)) })}
      />
      <div className="mt-2">
        <ErrorNotice error={save.error} />
      </div>
    </Card>
  );
}

function ChannelsCard() {
  const { t } = useTranslation();
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: () => api.get<ChannelsResponse>("/notification-channels") });
  if (channels.isLoading) return <Spinner />;
  if (!channels.data) return <ErrorNotice error={channels.error} />;
  return (
    <Card title={t("notifications.channels.title")}>
      <p className="mb-3 text-sm text-muted">{t("notifications.channels.intro")}</p>
      {!channels.data.webhooks_available && (
        <p className="mb-3 rounded-md bg-surface-2 px-3 py-2 text-xs text-muted">{t("notifications.channels.disabled")}</p>
      )}
      <div className="space-y-6">
        {channels.data.channels.map((c) => (
          <ChannelForm key={c.kind} channel={c} />
        ))}
      </div>
    </Card>
  );
}

function ChannelForm({ channel }: { channel: ChannelState }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(channel.configured ? channel.events : ["approval.routed", "approval.escalated"]);
  const [enabled, setEnabled] = useState(channel.configured ? channel.enabled : true);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["notification-channels"] });
  const save = useMutation({
    mutationFn: () => api.put(`/notification-channels/${channel.kind}`, { ...(url ? { webhook_url: url } : {}), events, enabled }),
    onSuccess: () => {
      setUrl("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => api.del(`/notification-channels/${channel.kind}`), onSuccess: refresh });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium">{t(`notifications.channels.${channel.kind}`)}</p>
        {channel.configured ? <Badge tone={channel.enabled ? "ok" : "neutral"}>{channel.webhook_host}</Badge> : <Badge>{t("notifications.channels.notConfigured")}</Badge>}
      </div>
      <Field label={t("notifications.channels.url")} hint={channel.configured ? t("notifications.channels.urlKeep") : t("notifications.channels.urlHint")}>
        <Input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" autoComplete="off" />
      </Field>
      <EventChecklist idPrefix={channel.kind} selected={events} onChange={setEvents} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-[var(--primary)]" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        {t("common.active")}
      </label>
      <ErrorNotice error={save.error ?? remove.error} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={save.isPending} disabled={!channel.configured && !url}>
          {t("common.save")}
        </Button>
        {channel.configured && (
          <Button type="button" size="sm" variant="ghost" loading={remove.isPending} onClick={() => remove.mutate()}>
            {t("notifications.channels.remove")}
          </Button>
        )}
      </div>
    </form>
  );
}

function DeadLettersCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const dead = useQuery({ queryKey: ["notification-deliveries", "dead"], queryFn: () => api.get<Delivery[]>("/notification-deliveries", { status: "dead" }) });
  const retry = useMutation({
    mutationFn: (id: string) => api.post(`/notification-deliveries/${id}/retry`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notification-deliveries"] }),
  });
  return (
    <Card title={t("notifications.dead.title")}>
      <p className="mb-3 text-sm text-muted">{t("notifications.dead.intro")}</p>
      <ErrorNotice error={dead.error ?? retry.error} />
      {dead.isLoading ? (
        <Spinner />
      ) : !dead.data?.length ? (
        <EmptyState title={t("notifications.dead.none")} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t("notifications.dead.event")}</Th>
              <Th>{t("notifications.dead.channel")}</Th>
              <Th>{t("notifications.dead.error")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {dead.data.map((d) => (
              <tr key={d.id}>
                <Td>
                  {t(`notifications.events.${d.event_type}.name` as never)}
                  <p className="text-xs text-muted">{relative(d.created_at)}</p>
                </Td>
                <Td>{t(`notifications.channels.${d.channel as ChannelKind | "email"}`)}</Td>
                <Td className="max-w-64 text-xs text-muted">
                  {d.last_error}
                  <p>{t("notifications.dead.attempts", { count: d.attempts })}</p>
                </Td>
                <Td>
                  <Button size="sm" variant="secondary" loading={retry.isPending && retry.variables === d.id} onClick={() => retry.mutate(d.id)}>
                    {t("notifications.dead.retry")}
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
