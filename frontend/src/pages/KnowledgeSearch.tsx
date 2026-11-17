import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Input, Textarea } from "../components/ui";
import { api } from "../lib/api";

type Citation = { id: string; title: string; excerpt: string; score: number };
type Answer = { answer: string; strategy: string; citations: Citation[]; generated?: boolean; model?: string | null };
type KnowledgeDocument = { id: string; title: string; version: number; is_active: boolean; source_reference: string | null };

/** Cited retrieval over policy rules and tenant documents (Japanese and English). */
export function KnowledgeSearch() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const [question, setQuestion] = useState("");
  const ask = useMutation({ mutationFn: (text: string) => api.post<Answer>("/procurement-assistant/knowledge", { question: text }) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (question.trim()) ask.mutate(question.trim());
  };
  return (
    <>
      <Card title={t("knowledge.title")} className="mt-5">
        <form onSubmit={submit} className="space-y-3">
          <Field label={t("assistant.question")} hint={t("knowledge.hint")}>
            <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} rows={2} placeholder={t("knowledge.placeholder")} />
          </Field>
          <Button loading={ask.isPending} disabled={!question.trim()}>
            <BookOpen className="size-4" /> {t("common.search")}
          </Button>
        </form>
        <ErrorNotice error={ask.error} />
        {ask.data && (
          <div className="mt-4">
            {ask.data.generated && (
              <p className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <Badge tone="info">{t("ai.summary.badge")}</Badge> {t("ai.summary.note", { model: ask.data.model ?? "" })}
              </p>
            )}
            <p className="text-sm font-medium">{ask.data.answer}</p>
            {ask.data.citations.length ? (
              <ol className="mt-3 space-y-3">
                {ask.data.citations.map((c) => (
                  <li key={c.id} className="rounded-md border border-border p-3">
                    <div className="flex flex-wrap justify-between gap-2 text-sm">
                      <span className="font-medium">{c.title}</span>
                      <span className="font-mono text-xs text-muted">{c.id}</span>
                    </div>
                    <p className="mt-1 text-sm text-muted">{c.excerpt}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <EmptyState title={t("knowledge.noMatchTitle")}>{t("knowledge.noMatchBody")}</EmptyState>
            )}
          </div>
        )}
      </Card>
      {can("policy:manage") && <KnowledgeDocuments />}
    </>
  );
}

function KnowledgeDocuments() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [content, setContent] = useState("");
  const documents = useQuery({ queryKey: ["knowledge-documents"], queryFn: () => api.get<KnowledgeDocument[]>("/procurement-assistant/knowledge/documents") });
  const add = useMutation({
    mutationFn: () => api.post("/procurement-assistant/knowledge/documents", { title, source_reference: source || null, content }),
    onSuccess: () => {
      setTitle("");
      setSource("");
      setContent("");
      void qc.invalidateQueries({ queryKey: ["knowledge-documents"] });
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate();
  };
  return (
    <Card title={t("knowledge.docsTitle")} className="mt-5">
      <p className="mb-3 text-sm text-muted">{t("knowledge.docsIntro")}</p>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        <Field label={t("requestForm.title")}><Input required minLength={3} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label={t("knowledge.source")}><Input maxLength={500} value={source} onChange={(e) => setSource(e.target.value)} placeholder={t("knowledge.sourcePlaceholder")} /></Field>
        <div className="md:col-span-2"><Field label={t("knowledge.text")}><Textarea required minLength={20} maxLength={100000} rows={5} value={content} onChange={(e) => setContent(e.target.value)} /></Field></div>
        <div><Button loading={add.isPending} disabled={title.trim().length < 3 || content.trim().length < 20}>{t("knowledge.add")}</Button></div>
      </form>
      <ErrorNotice error={add.error} />
      {documents.data?.length ? (
        <ul className="mt-4 divide-y divide-border text-sm">
          {documents.data.map((d) => (
            <li key={d.id} className="flex flex-wrap justify-between gap-2 py-2">
              <span>{d.title} <span className="text-muted">v{d.version}</span></span>
              <span className="text-xs text-muted">{d.is_active ? t("common.active") : t("common.inactive")}{d.source_reference ? ` · ${d.source_reference}` : ""}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
