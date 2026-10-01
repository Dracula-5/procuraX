import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/useAuth";
import { Button, Card, EmptyState, ErrorNotice, Field, Input, Textarea } from "../components/ui";
import { api } from "../lib/api";

type Citation = { id: string; title: string; excerpt: string; score: number };
type Answer = { answer: string; strategy: string; citations: Citation[] };
type KnowledgeDocument = { id: string; title: string; version: number; is_active: boolean; source_reference: string | null };

/** Cited retrieval over policy rules and tenant documents (Japanese and English). */
export function KnowledgeSearch() {
  const { can } = useAuth();
  const [question, setQuestion] = useState("");
  const ask = useMutation({ mutationFn: (text: string) => api.post<Answer>("/procurement-assistant/knowledge", { question: text }) });
  const submit = (event: FormEvent) => { event.preventDefault(); if (question.trim()) ask.mutate(question.trim()); };
  return <>
    <Card title="Policy and guidance search" className="mt-5">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Question" hint="Searches the approval policy rules and your organisation's documents. Returns cited excerpts, never a generated answer.">
          <Textarea value={question} onChange={e => setQuestion(e.target.value)} maxLength={500} rows={2} placeholder="適格請求書の記載事項は？ / When is a second quote required?" />
        </Field>
        <Button loading={ask.isPending} disabled={!question.trim()}><BookOpen className="size-4" /> Search</Button>
      </form>
      <ErrorNotice error={ask.error} />
      {ask.data && <div className="mt-4">
        <p className="text-sm font-medium">{ask.data.answer}</p>
        {ask.data.citations.length ? <ol className="mt-3 space-y-3">{ask.data.citations.map(c => <li key={c.id} className="rounded-md border border-border p-3"><div className="flex flex-wrap justify-between gap-2 text-sm"><span className="font-medium">{c.title}</span><span className="font-mono text-xs text-muted">{c.id}</span></div><p className="mt-1 text-sm text-muted">{c.excerpt}</p></li>)}</ol> : <EmptyState title="No cited source matched">Try different wording or add the relevant guideline.</EmptyState>}
      </div>}
    </Card>
    {can("policy:manage") && <KnowledgeDocuments />}
  </>;
}

function KnowledgeDocuments() {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [content, setContent] = useState("");
  const documents = useQuery({ queryKey: ["knowledge-documents"], queryFn: () => api.get<KnowledgeDocument[]>("/procurement-assistant/knowledge/documents") });
  const add = useMutation({
    mutationFn: () => api.post("/procurement-assistant/knowledge/documents", { title, source_reference: source || null, content }),
    onSuccess: () => { setTitle(""); setSource(""); setContent(""); void qc.invalidateQueries({ queryKey: ["knowledge-documents"] }); },
  });
  const submit = (event: FormEvent) => { event.preventDefault(); add.mutate(); };
  return <Card title="Organisation guidelines" className="mt-5">
    <p className="mb-3 text-sm text-muted">Plain-text policies or guidance (Japanese or English). Adding a document with an existing title creates a new version; earlier citations keep their version.</p>
    <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
      <Field label="Title"><Input required minLength={3} maxLength={200} value={title} onChange={e => setTitle(e.target.value)} /></Field>
      <Field label="Source (optional)"><Input maxLength={500} value={source} onChange={e => setSource(e.target.value)} placeholder="出典：国税庁「…」を加工して作成" /></Field>
      <div className="md:col-span-2"><Field label="Text"><Textarea required minLength={20} maxLength={100000} rows={5} value={content} onChange={e => setContent(e.target.value)} /></Field></div>
      <div><Button loading={add.isPending} disabled={title.trim().length < 3 || content.trim().length < 20}>Add document</Button></div>
    </form>
    <ErrorNotice error={add.error} />
    {documents.data?.length ? <ul className="mt-4 divide-y divide-border text-sm">{documents.data.map(d => <li key={d.id} className="flex flex-wrap justify-between gap-2 py-2"><span>{d.title} <span className="text-muted">v{d.version}</span></span><span className="text-xs text-muted">{d.is_active ? "active" : "inactive"}{d.source_reference ? ` · ${d.source_reference}` : ""}</span></li>)}</ul> : null}
  </Card>;
}
