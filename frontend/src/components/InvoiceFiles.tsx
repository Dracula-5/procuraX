import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Paperclip } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { downloadStored, toBase64 } from "../lib/files";
import { date, number } from "../lib/format";
import { Badge, Button, Card, EmptyState, ErrorNotice, Field, Select, Spinner, type Tone } from "./ui";

type InvoiceRef = { id: string; invoice_number: string };
type StoredFile = {
  id: string;
  invoice_id: string | null;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number;
  scan_status: "pending" | "clean" | "infected" | "not_scanned";
  scan_detail: string | null;
  retain_until: string;
  created_at: string;
};

const SCAN_TONE: Record<StoredFile["scan_status"], Tone> = { pending: "info", clean: "ok", infected: "danger", not_scanned: "warn" };

/** Source files of an invoice: stored once (with SHA-256), virus-scanned, attached permanently. */
export function InvoiceFiles({ invoices, canUpload }: { invoices: InvoiceRef[]; canUpload: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [invoiceId, setInvoiceId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const files = useQuery({
    queryKey: ["invoice-documents", invoiceId],
    queryFn: () => api.get<StoredFile[]>("/invoice-documents", { invoice_id: invoiceId }),
    enabled: Boolean(invoiceId),
  });
  const attach = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t("p2p.files.chooseFile"));
      const stored = await api.post<StoredFile>("/invoice-documents", { filename: file.name, content_base64: await toBase64(file) });
      return api.post<StoredFile>(`/invoices/${invoiceId}/documents`, { document_id: stored.id });
    },
    onSuccess: () => {
      setFile(null);
      void qc.invalidateQueries({ queryKey: ["invoice-documents", invoiceId] });
    },
  });
  const download = useMutation({
    mutationFn: (doc: StoredFile) => downloadStored(`/invoice-documents/${doc.id}/download`, doc.original_filename),
  });

  return (
    <Card title={t("p2p.files.title")} className="xl:col-span-2">
      <p className="mb-3 text-sm text-muted">{t("p2p.files.intro")}</p>
      <div className="grid gap-3 md:grid-cols-[minmax(0,240px)_1fr] md:items-end">
        <Field label={t("p2p.invoices.invoice")}>
          <Select value={invoiceId} onChange={(e) => setInvoiceId(e.target.value)}>
            <option value="">{t("common.selectPlaceholder")}</option>
            {invoices.map((i) => (
              <option key={i.id} value={i.id}>
                {i.invoice_number}
              </option>
            ))}
          </Select>
        </Field>
        {canUpload && invoiceId && (
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">
              {t("p2p.files.file")}
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1 block text-sm" />
            </label>
            <Button size="sm" loading={attach.isPending} disabled={!file} onClick={() => attach.mutate()}>
              <Paperclip className="size-4" /> {t("p2p.files.attach")}
            </Button>
          </div>
        )}
      </div>
      <div className="mt-3">
        <ErrorNotice error={attach.error ?? download.error ?? files.error} />
      </div>
      {!invoiceId ? null : files.isLoading ? (
        <Spinner />
      ) : !files.data?.length ? (
        <EmptyState title={t("p2p.files.none")} />
      ) : (
        <ul className="mt-3 divide-y divide-border">
          {files.data.map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-start justify-between gap-3 py-2 text-sm">
              <div className="min-w-0">
                <p className="font-medium">{doc.original_filename}</p>
                <p className="text-xs text-muted">
                  {t("p2p.files.meta", { size: number(doc.size_bytes / 1024, 0), pages: doc.page_count, retain: date(doc.retain_until) })}
                </p>
                <p className="font-mono text-[11px] text-muted" title={doc.sha256}>
                  SHA-256 {doc.sha256.slice(0, 16)}…
                </p>
                {doc.scan_detail && <p className="text-xs text-muted">{doc.scan_detail}</p>}
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={SCAN_TONE[doc.scan_status]}>{t(`p2p.files.scan.${doc.scan_status}`)}</Badge>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={doc.scan_status === "infected" || doc.scan_status === "pending"}
                  loading={download.isPending && download.variables?.id === doc.id}
                  onClick={() => download.mutate(doc)}
                  aria-label={t("p2p.files.download", { name: doc.original_filename })}
                >
                  <Download className="size-4" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
