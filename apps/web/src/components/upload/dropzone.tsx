/**
 * ParseGrid — Drag-and-drop file upload component.
 */

"use client";

import { useCallback, useState, type DragEvent } from "react";

interface DropzoneProps {
  onFileSelected: (file: File) => void;
  isUploading?: boolean;
  accept?: string;
  maxSizeMB?: number;
}

export function Dropzone({
  onFileSelected,
  isUploading = false,
  accept = ".pdf,.png,.jpg,.jpeg,.tiff,.bmp",
  maxSizeMB = 100,
}: DropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validateAndSelect = useCallback(
    (file: File) => {
      setError(null);

      // Validate size
      if (file.size > maxSizeMB * 1024 * 1024) {
        setError(`File too large. Maximum size: ${maxSizeMB}MB`);
        return;
      }

      // Validate type
      const ext = file.name.split(".").pop()?.toLowerCase();
      const allowedExts = accept
        .split(",")
        .map((s) => s.trim().replace(".", ""));
      if (ext && !allowedExts.includes(ext)) {
        setError(`Unsupported format. Allowed: ${accept}`);
        return;
      }

      onFileSelected(file);
    },
    [onFileSelected, maxSizeMB, accept],
  );

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) validateAndSelect(file);
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
        rounded-2xl border-2 border-dashed p-12
        transition-all duration-300 cursor-pointer
        ${
          isDragging
            ? "border-indigo-400 bg-indigo-500/10 scale-[1.02]"
            : "border-zinc-700 bg-zinc-900/50 hover:border-zinc-500 hover:bg-zinc-800/50"
        }
        ${isUploading ? "pointer-events-none opacity-60" : ""}
      `}
      onClick={() => {
        if (!isUploading) {
          const input = document.createElement("input");
          input.type = "file";
          input.accept = accept;
          input.onchange = (e) => {
            const file = (e.target as HTMLInputElement).files?.[0];
            if (file) validateAndSelect(file);
          };
          input.click();
        }
      }}
    >
      {/* Upload Icon */}
      <div
        className={`mb-4 rounded-full p-4 transition-colors ${
          isDragging ? "bg-indigo-500/20" : "bg-zinc-800"
        }`}
      >
        <svg
          className={`h-8 w-8 ${isDragging ? "text-indigo-400" : "text-zinc-400"}`}
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

      <p className="text-lg font-medium text-zinc-200">
        {isDragging ? "Drop your document here" : "Drag & drop your document"}
      </p>
      <p className="mt-1 text-sm text-zinc-500">
        or click to browse • PDF, PNG, JPG, TIFF
      </p>
      <p className="mt-1 text-xs text-zinc-600">Max {maxSizeMB}MB</p>

      {isUploading && (
        <div className="mt-4 flex items-center gap-2 text-indigo-400">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
              fill="none"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          <span className="text-sm">Uploading...</span>
        </div>
      )}

      {error && (
        <p className="mt-3 text-sm font-medium text-red-400">{error}</p>
      )}
    </div>
  );
}
