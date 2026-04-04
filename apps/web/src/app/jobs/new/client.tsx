/**
 * ParseGrid — New job client component.
 */

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Dropzone } from "@/components/upload/dropzone";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface NewJobClientProps {
  token: string | null;
}

export function NewJobClient({ token }: NewJobClientProps) {
  const router = useRouter();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [outputFormat, setOutputFormat] = useState("SQL");
  const [error, setError] = useState<string | null>(null);

  const handleFileSelected = (file: File) => {
    setSelectedFile(file);
    setError(null);
  };

  const handleSubmit = async () => {
    if (!selectedFile || !token) return;

    setIsUploading(true);
    setError(null);

    try {
      // Upload file to FastAPI
      const formData = new FormData();
      formData.append("file", selectedFile);

      const uploadRes = await fetch(`${API_BASE}/api/v1/upload/direct`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (!uploadRes.ok) throw new Error("Upload failed");
      const { file_key } = await uploadRes.json();

      // Create the extraction job
      const jobRes = await fetch(`${API_BASE}/api/v1/jobs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          filename: selectedFile.name,
          file_key,
          file_size: selectedFile.size,
          output_format: outputFormat,
        }),
      });

      if (!jobRes.ok) throw new Error("Job creation failed");
      const job = await jobRes.json();

      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
      setIsUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-12 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">
          New Extraction Job
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Upload a document and ParseGrid will extract structured data using AI.
        </p>
      </div>

      {/* Upload */}
      <Dropzone
        onFileSelected={handleFileSelected}
        isUploading={isUploading}
      />

      {/* Selected File Info */}
      {selectedFile && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-indigo-500/20 p-2">
              <svg
                className="h-5 w-5 text-indigo-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
                />
              </svg>
            </div>
            <div>
              <p className="text-sm font-medium text-zinc-200">
                {selectedFile.name}
              </p>
              <p className="text-xs text-zinc-500">
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
          </div>
          <button
            onClick={() => setSelectedFile(null)}
            className="text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
      )}

      {/* Output Format */}
      <div className="space-y-3">
        <label className="text-sm font-medium text-zinc-300">
          Output Format
        </label>
        <div className="flex gap-3">
          {["SQL", "GRAPH", "VECTOR"].map((format) => (
            <button
              key={format}
              onClick={() => setOutputFormat(format)}
              className={`rounded-xl border px-5 py-2.5 text-sm font-medium transition-colors ${
                outputFormat === format
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-400"
                  : "border-zinc-700 text-zinc-400 hover:border-zinc-600"
              }`}
            >
              {format}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={!selectedFile || isUploading || !token}
        className="w-full rounded-xl bg-indigo-600 py-3 text-sm font-semibold text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isUploading ? "Processing..." : "Upload & Start Extraction"}
      </button>
    </div>
  );
}
