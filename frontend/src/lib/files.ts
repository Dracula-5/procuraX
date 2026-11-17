import { api } from "./api";

/** File contents as base64 (chunked, so large files do not overflow the argument list). */
export async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function saveBase64(contentBase64: string, filename: string, type: string) {
  const bytes = Uint8Array.from(atob(contentBase64), (ch) => ch.charCodeAt(0));
  saveBlob(new Blob([bytes], { type }), filename);
}

/** Permission-checked download: object storage answers with a short-lived signed link, local storage with the bytes. */
export async function downloadStored(path: string, filename: string) {
  const blob = await api.blob(path, { mode: "link" });
  if (blob.type.startsWith("application/json")) {
    const { url } = JSON.parse(await blob.text()) as { url: string };
    window.open(url, "_blank", "noopener");
  } else {
    saveBlob(blob, filename);
  }
}
