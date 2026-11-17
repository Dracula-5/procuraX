import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Badge, Button, Card, ErrorNotice, Field, Input, PageHeader, Spinner } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { dateTime, money, percent } from "../lib/format";
import { useLabels } from "../lib/labels";
import type { PolicyConfig, PolicyVersion, Rule } from "../lib/types";

export function Policy() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { can } = useAuth();
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["policy-versions"], queryFn: () => api.get<PolicyVersion[]>("/approval-policies") });
  const rules = useQuery({ queryKey: ["policy-rules"], queryFn: () => api.get<Rule[]>("/approval-policies/rules") });
  const active = versions.data?.find((v) => v.is_active);
  // Unsaved edits overlay the active config; the form value is derived, not copied.
  const [edits, setEdits] = useState<Partial<PolicyConfig>>({});
  const [notes, setNotes] = useState("");
  const draft: PolicyConfig | null = active ? { ...active.config, ...edits } : null;

  const publish = useMutation({
    mutationFn: () => api.post<PolicyVersion>("/approval-policies", { config: draft, notes: notes || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policy-versions"] });
      setNotes("");
      setEdits({});
    },
  });
  const errors = publish.error instanceof ApiError ? publish.error.fieldErrors() : {};

  if (versions.isLoading || !active) return <Spinner />;
  const c = active.config;
  const cur = c.currency;
  const set = (k: keyof PolicyConfig) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const isMoney = k.endsWith("limit") || k.endsWith("threshold"); // decimals travel as strings
    setEdits((d) => ({ ...d, [k]: isMoney ? e.target.value : Number(e.target.value) }));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    publish.mutate();
  };
  const categories = (keys: string[]) => keys.map((x) => labels.category(x)).join(t("common.listSeparator")) || "—";

  return (
    <>
      <PageHeader title={t("policy.title")} subtitle={t("policy.subtitle")} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title={<span>{t("policy.active", { version: active.version })}</span>} actions={<Badge tone="ok">{t("policy.activeSince", { when: dateTime(active.created_at) })}</Badge>}>
          <dl className="space-y-2 text-sm">
            <Row k={t("policy.row.auto")} v={money(c.auto_approval_limit, cur)} />
            <Row k={t("policy.row.manager")} v={money(c.manager_approval_limit, cur)} />
            <Row k={t("policy.row.head")} v={money(c.manager_approval_limit, cur)} />
            <Row k={t("policy.row.procurement")} v={money(c.procurement_review_threshold, cur)} />
            <Row k={t("policy.row.alwaysProcurement")} v={categories(c.procurement_review_categories)} />
            <Row k={t("policy.row.contract")} v={categories(c.contract_required_categories)} />
            <Row k={t("policy.row.budgetWarning")} v={t("policy.row.budgetWarningValue", { percent: percent(c.budget_warning_utilization, 0) })} />
            <Row k={t("policy.row.split")} v={t("policy.row.days", { count: c.split_purchase_window_days })} />
            <Row k={t("policy.row.emergency")} v={t("policy.row.chars", { count: c.emergency_min_justification_chars })} />
            <Row k={t("policy.row.sla")} v={t("policy.row.hours", { count: c.approval_sla_hours })} />
          </dl>
          {active.notes && <p className="mt-3 text-xs text-muted">{active.notes}</p>}
        </Card>

        {can("policy:manage") && draft && (
          <Card title={t("policy.publishTitle")}>
            <form onSubmit={submit} className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label={t("policy.form.auto", { currency: cur })} error={errors.auto_approval_limit}>
                  <Input type="number" min={0} value={draft.auto_approval_limit} onChange={set("auto_approval_limit")} />
                </Field>
                <Field label={t("policy.form.manager", { currency: cur })} error={errors.manager_approval_limit}>
                  <Input type="number" min={0} value={draft.manager_approval_limit} onChange={set("manager_approval_limit")} />
                </Field>
                <Field label={t("policy.form.procurement", { currency: cur })} error={errors.procurement_review_threshold}>
                  <Input type="number" min={0} value={draft.procurement_review_threshold} onChange={set("procurement_review_threshold")} />
                </Field>
                <Field label={t("policy.form.split")}>
                  <Input type="number" min={0} max={90} value={draft.split_purchase_window_days} onChange={set("split_purchase_window_days")} />
                </Field>
                <Field label={t("policy.form.sla")}>
                  <Input type="number" min={1} max={720} value={draft.approval_sla_hours} onChange={set("approval_sla_hours")} />
                </Field>
              </div>
              <Field label={t("policy.form.note")}>
                <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("policy.form.notePlaceholder")} />
              </Field>
              {errors.config && <p className="text-sm text-danger">{errors.config}</p>}
              {!Object.keys(errors).length && <ErrorNotice error={publish.error} />}
              <Button type="submit" loading={publish.isPending}>
                {t("policy.form.publish", { version: active.version + 1 })}
              </Button>
            </form>
          </Card>
        )}

        <Card title={t("policy.catalogue")} className="lg:col-span-2">
          <ul className="grid gap-2 sm:grid-cols-2">
            {rules.data?.map((r) => (
              <li key={r.id} className="rounded-md border border-border p-3 text-sm">
                <p>
                  <span className="font-mono text-xs text-muted">{r.id}</span> <span className="font-medium">{labels.ruleTitle(r.id, r.title)}</span>
                </p>
                <p className="mt-1 text-xs text-muted">{labels.ruleDescription(r.id, r.description)}</p>
              </li>
            ))}
          </ul>
        </Card>

        <KnowledgeDocuments canManage={can("policy:manage")} />

        <Card title={t("policy.history")} className="lg:col-span-2">
          <ul className="divide-y divide-border text-sm">
            {versions.data?.map((v) => (
              <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">v{v.version}</span> <span className="text-muted">{v.notes}</span>
                </span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {v.is_active && <Badge tone="ok">{t("common.active")}</Badge>}
                  {dateTime(v.created_at)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}

type KnowledgeDocument = { id: string; title: string; source_reference: string | null; version: number; is_active: boolean; created_at: string };

function KnowledgeDocuments({ canManage }: { canManage: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [content, setContent] = useState("");
  const documents = useQuery({ queryKey: ["knowledge-documents"], queryFn: () => api.get<KnowledgeDocument[]>("/procurement-assistant/knowledge/documents") });
  const add = useMutation({
    mutationFn: () => api.post<KnowledgeDocument>("/procurement-assistant/knowledge/documents", { title, source_reference: sourceReference || null, content }),
    onSuccess: () => {
      setTitle("");
      setSourceReference("");
      setContent("");
      void qc.invalidateQueries({ queryKey: ["knowledge-documents"] });
    },
  });
  const deactivate = useMutation({
    mutationFn: (id: string) => api.post(`/procurement-assistant/knowledge/documents/${id}/deactivate`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["knowledge-documents"] }),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };
  return (
    <Card title={t("policy.sources.title")} className="lg:col-span-2">
      <p className="mb-4 text-sm text-muted">{t("policy.sources.intro")}</p>
      {canManage && (
        <form onSubmit={submit} className="mb-5 grid gap-3 md:grid-cols-2">
          <Field label={t("policy.sources.docTitle")}><Input required minLength={3} maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></Field>
          <Field label={t("policy.sources.reference")}><Input maxLength={500} value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} placeholder={t("policy.sources.referencePlaceholder")} /></Field>
          <Field label={t("policy.sources.content")}><textarea required minLength={20} maxLength={100000} value={content} onChange={(event) => setContent(event.target.value)} className="min-h-32 rounded-md border border-border bg-surface px-3 py-2 text-sm" /></Field>
          <div className="flex items-end gap-3"><Button type="submit" loading={add.isPending}>{t("policy.sources.addVersion")}</Button><ErrorNotice error={add.error} /></div>
        </form>
      )}
      <ErrorNotice error={documents.error ?? deactivate.error} />
      {documents.isLoading ? (
        <Spinner />
      ) : !documents.data?.length ? (
        <p className="text-sm text-muted">{t("policy.sources.none")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {documents.data.map((document) => (
            <li key={document.id} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
              <span>
                <span className="font-medium">{document.title} v{document.version}</span>
                <span className="ml-2 text-xs text-muted">{document.source_reference ?? t("policy.sources.noReference")}</span>
              </span>
              <span className="flex items-center gap-2">
                {document.is_active ? <Badge tone="ok">{t("common.active")}</Badge> : <Badge>{t("common.inactive")}</Badge>}
                {canManage && document.is_active && (
                  <Button size="sm" variant="secondary" loading={deactivate.isPending} onClick={() => deactivate.mutate(document.id)}>
                    {t("policy.sources.deactivate")}
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{k}</dt>
      <dd className="text-right font-medium">{v}</dd>
    </div>
  );
}
