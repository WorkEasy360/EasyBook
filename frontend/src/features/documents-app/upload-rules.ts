/**
 * Client-side upload pre-check — for IMMEDIACY only.
 *
 * Mirrors backend/documents/services/validation.py so an obviously wrong file
 * is refused before its bytes cross the network. The backend re-validates
 * everything (extension, canonical MIME, magic bytes, size) and its rejection
 * is what the dialog shows when it disagrees with this file. Never relax a
 * rule here that the server enforces, and never add one it does not.
 */

export type UploadSizeCategory = "pdf" | "image" | "default";

/** validation.py :: ALLOWED_TYPES — extension → canonical MIME and size category. */
export const ALLOWED_UPLOAD_TYPES: Record<string, { mime: string; category: UploadSizeCategory }> = {
  ".pdf": { mime: "application/pdf", category: "pdf" },
  ".png": { mime: "image/png", category: "image" },
  ".jpg": { mime: "image/jpeg", category: "image" },
  ".jpeg": { mime: "image/jpeg", category: "image" },
  ".csv": { mime: "text/csv", category: "default" },
  ".txt": { mime: "text/plain", category: "default" },
};

/**
 * settings/base.py :: DOCUMENT_MAX_UPLOAD_SIZES DEFAULTS (env-overridable per
 * deployment, and not exposed by any endpoint). If an operator lowers a limit
 * the server's `file_too_large` message is shown; if they raise one, this
 * pre-check would refuse a file the server accepts — keep them in step.
 */
export const DEFAULT_MAX_UPLOAD_BYTES: Record<UploadSizeCategory, number> = {
  default: 10 * 1024 * 1024,
  image: 15 * 1024 * 1024,
  pdf: 25 * 1024 * 1024,
};

/** For the file input's `accept` attribute. */
export const UPLOAD_ACCEPT = Object.keys(ALLOWED_UPLOAD_TYPES).join(",");

export const ALLOWED_UPLOAD_DESCRIPTION = "PDF, PNG, JPG, CSV or TXT";

export interface UploadCandidate {
  name: string;
  size: number;
  /** File.type as the browser reports it; "" when the OS does not know. */
  type: string;
}

export type UploadPrecheck =
  | { ok: true; extension: string; mime: string }
  | { ok: false; message: string };

function extensionOf(name: string): string {
  // The server takes the suffix of the sanitized base name; for the purpose
  // of a pre-check the last dot of the client name is equivalent.
  const base = name.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

export function precheckUpload(file: UploadCandidate): UploadPrecheck {
  const extension = extensionOf(file.name);
  const allowed = ALLOWED_UPLOAD_TYPES[extension];
  if (!allowed) {
    return {
      ok: false,
      message: `File type ${extension || "(none)"} is not allowed. Upload a ${ALLOWED_UPLOAD_DESCRIPTION} file.`,
    };
  }

  if (file.size === 0) return { ok: false, message: "This file is empty." };

  const limit = DEFAULT_MAX_UPLOAD_BYTES[allowed.category];
  if (file.size > limit) {
    return { ok: false, message: `This file is larger than the ${megabytes(limit)} limit for ${extension} files.` };
  }

  // The browser sends File.type as the part's Content-Type, or
  // application/octet-stream when it is empty — and the server refuses any
  // declared type that is not the extension's canonical one (`mime_mismatch`).
  const declared = (file.type || "application/octet-stream").split(";")[0]?.trim().toLowerCase() ?? "";
  if (declared !== allowed.mime) {
    return {
      ok: false,
      message: `Your browser identifies this file as "${declared}", but a ${extension} file must be ${allowed.mime}. The server checks that the two agree and would refuse it.`,
    };
  }

  return { ok: true, extension, mime: allowed.mime };
}
