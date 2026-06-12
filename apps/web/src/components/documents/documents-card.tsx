/**
 * ParseGrid — Dataset documents list with per-file status and append entry.
 */

"use client";

import {useRef, useState} from "react";
import {TrashIcon} from "@phosphor-icons/react/dist/ssr/Trash";
import type {JobDocument} from "@/lib/api-client";
import {StatusBadge} from "@/components/job-status/status-badge";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DocumentsCardProps {
  documents: JobDocument[];
  canAppend: boolean;
  canDelete: boolean;
  token: string | null;
  onAppend: (file: {filename: string; file_key: string; file_size: number}) => void;
  onDelete: (documentId: string) => void;
  isAppending: boolean;
}

export function DocumentsCard({
  documents,
  canAppend,
  canDelete,
  token,
  onAppend,
  onDelete,
  isAppending,
}: DocumentsCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFile = async (file: File) => {
    if (!token) return;
    setIsUploading(true);
    setUploadError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/api/v1/upload/direct`, {
        method: "POST",
        headers: {Authorization: `Bearer ${token}`},
        body: formData,
      });
      if (!res.ok) throw new Error(`Upload failed: ${file.name}`);
      const {file_key} = await res.json();
      onAppend({filename: file.name, file_key, file_size: file.size});
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setIsUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className='rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6'>
      <div className='flex items-center justify-between'>
        <h3 className='text-xs font-semibold uppercase tracking-wider text-zinc-500'>
          Documents ({documents.length})
        </h3>
        {canAppend && (
          <>
            <input
              ref={inputRef}
              type='file'
              accept='.pdf,.png,.jpg,.jpeg,.tiff,.bmp'
              className='hidden'
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
            />
            <button
              onClick={() => inputRef.current?.click()}
              disabled={isUploading || isAppending}
              className='rounded-xl border border-emerald-600/40 bg-emerald-600/10 px-4 py-2 text-sm font-medium text-emerald-400 transition-all hover:bg-emerald-600/20 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50'>
              {isUploading || isAppending ? "Adding…" : "Add data"}
            </button>
          </>
        )}
      </div>
      {uploadError && (
        <div className='mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-2 text-sm text-red-400'>
          {uploadError}
        </div>
      )}
      <ul className='mt-4 divide-y divide-zinc-800/60'>
        {documents.map((doc) => (
          <li key={doc.id} className='flex items-center justify-between gap-3 py-3'>
            <div className='min-w-0'>
              <p className='truncate text-sm font-medium text-zinc-200'>{doc.filename}</p>
              <p className='text-xs font-mono text-zinc-500'>
                {(doc.file_size / 1024 / 1024).toFixed(2)} MB
                {doc.page_count ? ` · ${doc.page_count} pages` : ""}
              </p>
            </div>
            <div className='flex items-center gap-2'>
              <StatusBadge status={doc.status} />
              {canDelete && documents.length > 1 && (
                <button
                  onClick={() => onDelete(doc.id)}
                  aria-label={`Remove ${doc.filename}`}
                  className='rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-red-300'>
                  <TrashIcon className='h-4 w-4' />
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
