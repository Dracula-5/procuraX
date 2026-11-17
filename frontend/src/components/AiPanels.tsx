import { useMutation, useQuery } from "@tanstack/react-query";
import { Database, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Table, Td, Textarea, Th } from "./ui";

type Flag = { code: string; severity: string; score?: number; explanation?: string; related_request?: string };
type Advisory = { prediction_id: string; model: string; model_version: string; created_at: string; submission: number | null; flags: Flag[] };

/** ML flags recorded at submission. For reviewers only; they never change the approval route. */
export function AdvisoryFlags({ requestId }: { requestId: string }) {
  const { t } = useTranslation();
  const advisories = useQuery({ queryKey: ["advisories", requestId], queryFn: () => api.get<Advisory[]>(`/purchase-requests/${requestId}/advisories`) });
  const latest = advisories.data?.[0];
  if (!latest) return null;
  return (
    <Card title={t("ai.flags.title")}>
      <p className="mb-3 text-xs text-muted">{t("ai.flags.note")}</p>
      {latest.flags.length === 0 ? (
        <p className="text-sm text-muted">{t("ai.flags.none")}</p>
      ) : (
        <ul className="space-y-2">
          {latest.flags.map((flag) => (
            <li key={flag.code + (flag.related_request ?? "")} className="rounded-md border border-border p-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge tone={flag.severity === "high" ? "danger" : flag.severity === "medium" ? "warn" : "info"}>{t(`ai.flags.codes.${flag.code}` as never, { defaultValue: flag.code })}</Badge>
                {flag.related_request && <span className="font-mono text-xs">{flag.related_request}</span>}
              </div>
              {flag.explanation && <p className="mt-1 text-xs text-muted">{flag.explanation}</p>}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">{t("ai.flags.source", { model: latest.model, version: latest.model_version, when: dateTime(latest.created_at) })}</p>
    </Card>
  );
}

type SqlAnswer = {
  status: "answered" | "refused" | "unanswerable" | "no_model_answer";
  sql: string | null;
  columns: string[];
  rows: (string | null)[][];
  reason: string | null;
  model: string | null;
};

/** A model drafts SQL; the server's guard checks it; it runs read-only within this organisation's data. */
export function SqlAnalysis() {
  const { t } = useTranslation();
  const [question, setQuestion] = useState("");
  const ask = useMutation({ mutationFn: (text: string) => api.post<SqlAnswer>("/procurement-assistant/sql-query", { question: text }) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (question.trim()) ask.mutate(question.trim());
  };
  const answer = ask.data;
  return (
    <Card title={t("ai.sql.title")} className="mt-5">
      <form onSubmit={submit} className="space-y-3">
        <Field label={t("assistant.question")} hint={t("ai.sql.hint")}>
          <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} rows={2} placeholder={t("ai.sql.placeholder")} />
        </Field>
        <Button loading={ask.isPending} disabled={!question.trim()}>
          <Database className="size-4" /> {t("ai.sql.run")}
        </Button>
      </form>
      <ErrorNotice error={ask.error} />
      {answer && (
        <div className="mt-4 space-y-3">
          <Badge tone={answer.status === "answered" ? "ok" : answer.status === "refused" ? "danger" : "neutral"}>{t(`ai.sql.status.${answer.status}`)}</Badge>
          {answer.reason && <p className="text-xs text-muted">{t(`ai.sql.reasons.${answer.reason.split(":")[0]}` as never, { defaultValue: answer.reason })}</p>}
          {answer.sql && <pre className="overflow-x-auto rounded-md bg-surface-2 p-3 text-xs">{answer.sql}</pre>}
          {answer.status === "answered" &&
            (answer.rows.length ? (
              <Table>
                <thead>
                  <tr>
                    {answer.columns.map((c) => (
                      <Th key={c}>{c}</Th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {answer.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((value, j) => (
                        <Td key={j} className="tabular">
                          {value ?? "—"}
                        </Td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <EmptyState title={t("ai.sql.noRows")} />
            ))}
          {answer.model && (
            <p className="flex items-center gap-1 text-xs text-muted">
              <Sparkles className="size-3" /> {t("ai.sql.drafted", { model: answer.model })}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
