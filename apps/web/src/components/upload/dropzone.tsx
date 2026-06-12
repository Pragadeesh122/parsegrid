/**
 * ParseGrid — Drag-and-drop file upload component.
 * Emerald accent on drag-over, clean minimal styling.
 */

"use client";

import { useCallback, useState, type DragEvent } from "react";

interface DropzoneProps {
  onFilesSelected: (files: File[]) => void;
  isUploading?: boolean;
  accept?: string;
  maxSizeMB?: number;
  multiple?: boolean;
}

export function Dropzone({
  onFilesSelected,
  isUploading = false,
  accept = ".pdf,.png,.jpg,.jpeg,.tiff,.bmp",
  maxSizeMB = 100,
  multiple = false,
}: DropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validateAndSelect = useCallback(
    (files: File[]) => {
      setError(null);
      const allowedExts = accept.split(",").map((s) => s.trim().replace(".", ""));
      const valid: File[] = [];
      for (const file of files) {
        if (file.size > maxSizeMB * 1024 * 1024) {
          setError(`${file.name}: too large. Maximum size: ${maxSizeMB}MB`);
          continue;
        }
        const ext = file.name.split(".").pop()?.toLowerCase();
        if (ext && !allowedExts.includes(ext)) {
          setError(`${file.name}: unsupported format. Allowed: ${accept}`);
          continue;
        }
        valid.push(file);
      }
      if (valid.length) onFilesSelected(multiple ? valid : valid.slice(0, 1));
    },
    [onFilesSelected, maxSizeMB, accept, multiple],
  );

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      validateAndSelect(Array.from(e.dataTransfer.files));
    },
    [validateAndSelect],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={`
        relative flex flex-col items-center justify-center
        rounded-2xl border-2 border-dashed p-14
        transition-all duration-200 cursor-pointer
        ${
          isDragging
            ? "border-emerald-500 bg-emerald-500/5 scale-[1.01]"
            : "border-zinc-800 bg-zinc-900/30 hover:border-zinc-700 hover:bg-zinc-900/50"
        }
        ${isUploading ? "pointer-events-none opacity-50" : ""}
      `}
      onClick={() => {
        if (!isUploading) {
          const input = document.createElement("input");
          input.type = "file";
          input.accept = accept;
          input.multiple = multiple;
          input.onchange = (e) => {
            const files = Array.from(
              (e.target as HTMLInputElement).files ?? [],
            );
            if (files.length) validateAndSelect(files);
          };
          input.click();
        }
      }}
    >
      <div
        className={`mb-4 rounded-xl p-3 transition-colors ${
          isDragging ? "bg-emerald-500/10" : "bg-zinc-800/60"
        }`}
      >
        <svg
          className={`h-6 w-6 ${isDragging ? "text-emerald-500" : "text-zinc-500"}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
          />
        </svg>
      </div>

      <p className="text-sm font-medium text-zinc-300">
        {isDragging
          ? multiple
            ? "Drop your documents here"
            : "Drop your document here"
          : multiple
            ? "Drag & drop your documents"
            : "Drag & drop your document"}
      </p>
      <p className="mt-1 text-xs text-zinc-500">
        or click to browse &middot; PDF, PNG, JPG, TIFF
      </p>
      <p className="mt-1 text-xs text-zinc-700">Max {maxSizeMB}MB</p>

      {isUploading && (
        <div className="mt-4 flex items-center gap-2 text-emerald-500">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="text-sm">Uploading...</span>
        </div>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-400">{error}</p>
      )}
    </div>
  );
}
