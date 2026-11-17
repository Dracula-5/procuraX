import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button, Card, EmptyState, ErrorNotice, Field, PageHeader, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { KnowledgeSearch } from "./KnowledgeSearch";

type Result = {
  intent: string | null;
  answer: string;
  strategy: string;
  evidence: { id: string; type: string; label: string; details: Record<string, unknown> }[];
};

export function Assistant() {
  const { t } = useTranslation();
  const [question, setQuestion] = useState("");
  const query = useMutation({ mutationFn: (text: string) => api.post<Result>("/procurement-assistant/query", { question: text }) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (question.trim()) query.mutate(question.trim());
  };
  return (
    <>
      <PageHeader title={t("assistant.title")} subtitle={t("assistant.subtitle")} />
      <Card title={t("assistant.askTitle")}>
        <form onSubmit={submit} className="space-y-3">
          <Field label={t("assistant.question")}>
            <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} rows={3} placeholder={t("assistant.placeholder")} />
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted">{t("assistant.try")}</p>
            <Button loading={query.isPending} disabled={!question.trim()}>
              <Search className="size-4" /> {t("assistant.query")}
            </Button>
          </div>
        </form>
      </Card>
      {query.isError && (
        <div className="mt-4">
          <ErrorNotice error={query.error} />
        </div>
      )}
      {query.data && (
        <Card title={query.data.intent?.replaceAll("_", " ") ?? t("assistant.supported")} className="mt-5">
          <p className="text-sm font-medium">{query.data.answer}</p>
          <p className="mt-2 text-xs text-muted">{query.data.strategy}</p>
          {query.data.evidence.length ? (
            <ul className="mt-4 divide-y divide-border">
              {query.data.evidence.map((row) => (
                <li key={row.id} className="py-3">
                  <div className="flex flex-wrap justify-between gap-2">
                    <span className="font-medium">{row.label}</span>
                    <span className="text-xs uppercase tracking-wide text-muted">{row.type}</span>
                  </div>
                  <dl className="mt-2 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                    {Object.entries(row.details).map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-2">
                        <dt className="text-muted">{key.replaceAll("_", " ")}</dt>
                        <dd className="text-right">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title={t("assistant.noMatchTitle")}>{t("assistant.noMatchBody")}</EmptyState>
          )}
        </Card>
      )}
      <KnowledgeSearch />
    </>
  );
}
