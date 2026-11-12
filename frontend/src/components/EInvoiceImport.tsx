import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileCode2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { useLabels } from "../lib/labels";
import { Badge, Button, Card, ErrorNotice } from "./ui";

type Imported = { invoice_number: string; match_status: string; match_details: { source?: { jp_pint?: boolean } } };

/** Upload a Peppol BIS / JP PINT UBL invoice; it runs through the same three-way match as manual entry. */
export function EInvoiceImport() {
  const { t } = useTranslation();
  const labels = useLabels();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t("p2p.files.chooseFile"));
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      return api.post<Imported>("/einvoices", { xml_base64: btoa(binary) });
    },
    onSuccess: () => {
      setFile(null);
      void qc.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
  return (
    <Card title={t("p2p.einvoice.title")} className="xl:col-span-2">
      <p className="mb-3 text-sm text-muted">{t("p2p.einvoice.intro")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          {t("p2p.einvoice.file")}
          <input type="file" accept=".xml,application/xml,text/xml" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1 block text-sm" />
        </label>
        <Button size="sm" loading={upload.isPending} disabled={!file} onClick={() => upload.mutate()}>
          <FileCode2 className="size-4" /> {t("p2p.einvoice.import")}
        </Button>
      </div>
      <div className="mt-3">
        <ErrorNotice error={upload.error} />
      </div>
      {upload.data && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {t("p2p.einvoice.imported", { number: upload.data.invoice_number })}
          <Badge tone={upload.data.match_status === "matched" ? "ok" : "warn"}>{labels.matchStatus(upload.data.match_status)}</Badge>
          {upload.data.match_details.source?.jp_pint && <Badge tone="info">JP PINT</Badge>}
        </p>
      )}
    </Card>
  );
}
