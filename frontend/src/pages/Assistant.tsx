import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
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
  const [question, setQuestion] = useState("");
  const query = useMutation({ mutationFn: (text: string) => api.post<Result>("/procurement-assistant/query", { question: text }) });
  const submit = (event: FormEvent) => { event.preventDefault(); if (question.trim()) query.mutate(question.trim()); };
  return <>
    <PageHeader title="Procurement assistant" subtitle="Answers use fixed, tenant-scoped queries and link each result to source records. Unsupported questions return no guessed answer." />
    <Card title="Ask about procurement data">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Question">
          <Textarea value={question} onChange={e => setQuestion(e.target.value)} maxLength={500} rows={3} placeholder="Show software purchases above ¥1,000,000" />
        </Field>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted">Try: unmatched invoices · approved vendors for software · pending payment approvals · why is PR-2026-000042 blocked?</p>
          <Button loading={query.isPending} disabled={!question.trim()}><Search className="size-4" /> Query</Button>
        </div>
      </form>
    </Card>
    {query.isError && <div className="mt-4"><ErrorNotice error={query.error} /></div>}
    {query.data && <Card title={query.data.intent?.replaceAll("_", " ") ?? "Supported query types"} className="mt-5">
      <p className="text-sm font-medium">{query.data.answer}</p>
      <p className="mt-2 text-xs text-muted">{query.data.strategy}</p>
      {query.data.evidence.length ? <ul className="mt-4 divide-y divide-border">{query.data.evidence.map(row => <li key={row.id} className="py-3"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{row.label}</span><span className="text-xs uppercase tracking-wide text-muted">{row.type}</span></div><dl className="mt-2 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">{Object.entries(row.details).map(([key, value]) => <div key={key} className="flex justify-between gap-2"><dt className="text-muted">{key.replaceAll("_", " ")}</dt><dd className="text-right">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></li>)}</ul> : <EmptyState title="No matching records">No evidence matched the supported query in this tenant.</EmptyState>}
    </Card>}
    <KnowledgeSearch />
  </>;
}

