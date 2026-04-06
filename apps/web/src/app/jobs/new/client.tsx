/**
 * ParseGrid — New job client component.
 */

"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
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
  const [jobType, setJobType] = useState<"FULL" | "TARGETED">("FULL");
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
      const formData = new FormData();
      formData.append("file", selectedFile);

      const uploadRes = await fetch(`${API_BASE}/api/v1/upload/direct`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (!uploadRes.ok) throw new Error("Upload failed");
      const { file_key } = await uploadRes.json();

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
          job_type: jobType,
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
    <AppShell>
      <div className="px-6 py-8 lg:px-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-sm">
          <Link
            href="/dashboard"
            className="text-zinc-500 transition-colors hover:text-zinc-300"
          >
            Dashboard
          </Link>
          <svg
            className="h-3.5 w-3.5 text-zinc-700"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8.25 4.5l7.5 7.5-7.5 7.5"
            />
          </svg>
          <span className="text-zinc-300">New Job</span>
        </div>

        <div className="mt-6 max-w-2xl space-y-8">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
              New Extraction Job
            </h1>
            <p className="mt-1 text-sm text-zinc-500">
              Upload a document and ParseGrid will extract structured data.
            </p>
          </div>

          <Dropzone
            onFileSelected={handleFileSelected}
            isUploading={isUploading}
          />

          {selectedFile && (
            <div className="flex items-center justify-between rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10">
                  <svg
                    className="h-4 w-4 text-emerald-500"
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
                  <p className="text-xs font-mono text-zinc-500">
                    {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                </div>
              </div>
              <button
                onClick={() => setSelectedFile(null)}
                className="rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
              >
                <svg
                  className="h-4 w-4"
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

          {/* Extraction Mode */}
          <div className="space-y-3">
            <label className="text-sm text-zinc-400">Extraction Mode</label>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => setJobType("FULL")}
                className={`rounded-xl border p-4 text-left transition-all active:scale-[0.98] ${
                  jobType === "FULL"
                    ? "border-emerald-600 bg-emerald-600/10"
                    : "border-zinc-800 hover:border-zinc-700"
                }`}
              >
                <span
                  className={`text-sm font-medium ${
                    jobType === "FULL" ? "text-emerald-400" : "text-zinc-300"
                  }`}
                >
                  Full Extraction
                </span>
                <p className="mt-1 text-xs text-zinc-500">
                  Process entire document. AI discovers schema automatically.
                </p>
              </button>
              <button
                onClick={() => setJobType("TARGETED")}
                className={`rounded-xl border p-4 text-left transition-all active:scale-[0.98] ${
                  jobType === "TARGETED"
                    ? "border-emerald-600 bg-emerald-600/10"
                    : "border-zinc-800 hover:border-zinc-700"
                }`}
              >
                <span
                  className={`text-sm font-medium ${
                    jobType === "TARGETED"
                      ? "text-emerald-400"
                      : "text-zinc-300"
                  }`}
                >
                  Targeted Extraction
                </span>
                <p className="mt-1 text-xs text-zinc-500">
                  Ask a question. Only relevant sections are extracted.
                </p>
              </button>
            </div>
          </div>

          {/* Output Format */}
          <div className="space-y-3">
            <label className="text-sm text-zinc-400">Output Format</label>
            <div className="flex gap-2">
              {["SQL", "GRAPH", "VECTOR"].map((format) => (
                <button
                  key={format}
                  onClick={() => setOutputFormat(format)}
                  className={`rounded-xl border px-5 py-2 text-sm font-medium transition-all active:scale-[0.98] ${
                    outputFormat === format
                      ? "border-emerald-600 bg-emerald-600/10 text-emerald-400"
                      : "border-zinc-800 text-zinc-400 hover:border-zinc-700"
                  }`}
                >
                  {format}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={!selectedFile || isUploading || !token}
            className="w-full rounded-xl bg-emerald-600 py-3 text-sm font-semibold text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isUploading ? "Processing..." : "Start Extraction"}
          </button>
        </div>
      </div>
    </AppShell>
  );
}
