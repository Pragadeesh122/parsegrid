/**
 * ParseGrid — Job detail client component with real-time SSE status.
 *
 * Uses TanStack Query for data fetching + SSE overlay for live progress.
 * Falls back to query invalidation on terminal status to pick up fields
 * SSE doesn't carry (connection_string, etc.).
 */

"use client";

import { useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { ProgressBar } from "@/components/job-status/progress-bar";
import { SchemaForm } from "@/components/schema-editor/schema-form";
import { ConnectionString } from "@/components/connection/conn-string";
import { DataPreview } from "@/components/data-preview/data-table";
import { useJob, useApproveSchema } from "@/hooks/use-jobs";
import { useSSE } from "@/hooks/use-sse";

export default function JobDetailClient({
  jobId,
  token,
}: {
  jobId: string;
  token: string | null;
}) {
  const queryClient = useQueryClient();
  const { data: job, isLoading: loading, error: queryError } = useJob(jobId, token ?? "");
  const approveMutation = useApproveSchema(token ?? "");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // SSE overlay — merges live progress into the cached query data
  const isProcessing =
    !!job &&
    job.status !== "COMPLETED" &&
    job.status !== "FAILED" &&
    job.status !== "SCHEMA_PROPOSED" &&
    job.status !== "AWAITING_REVIEW";

  useSSE({
    jobId,
    enabled: isProcessing,
    onStatus: (data) => {
      queryClient.setQueryData(["job", jobId], (prev: typeof job) =>
        prev
          ? {
              ...prev,
              status: data.status,
              progress: data.progress,
              error_message: data.error_message ?? prev.error_message,
              connection_string: data.connection_string ?? prev.connection_string,
            }
          : prev,
      );

      if (data.status === "COMPLETED" || data.status === "FAILED") {
        queryClient.invalidateQueries({ queryKey: ["job", jobId] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }
    },
  });

  const handleApproveSchema = async (
    editedSchema: Record<string, unknown>,
  ) => {
    setIsSubmitting(true);
    try {
      await approveMutation.mutateAsync({ jobId, schema: editedSchema });
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
    } catch (e) {
      console.error("Schema approval failed:", e);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRejectSchema = async () => {
    setIsSubmitting(true);
    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(
        `${API_BASE}/api/v1/jobs/${jobId}/reject-schema`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        },
      );
      if (!res.ok) throw new Error("Schema rejection failed");
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      console.error("Schema rejection failed:", e);
    } finally {
      setIsSubmitting(false);
    }
  };

  const error = queryError ? (queryError as Error).message : null;

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-12 text-center space-y-4">
        <p className="text-red-400">{error || "Job not found"}</p>
        <Link
          href="/dashboard"
          className="text-sm text-zinc-400 hover:text-zinc-100"
        >
          Back to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] flex flex-col">
      {/* Nav */}
      <nav className="sticky top-0 z-30 border-b border-zinc-800/60 bg-zinc-950/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <Link href="/dashboard" className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="text-base font-semibold tracking-tight text-zinc-100">
                ParseGrid
              </span>
            </Link>
            <span className="text-zinc-700">/</span>
            <span className="text-sm text-zinc-400 truncate max-w-[200px]">
              {job.filename}
            </span>
          </div>
          <Link
            href="/dashboard"
            className="text-sm text-zinc-400 transition-colors hover:text-zinc-100"
          >
            All Jobs
          </Link>
        </div>
      </nav>

      <main className="flex-1">
        <div className="mx-auto max-w-5xl px-6 py-10 space-y-8">
          {/* Header */}
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-100">
              {job.filename}
            </h1>
            <p className="mt-1 text-xs font-mono text-zinc-600">{job.id}</p>
          </div>

          {/* Progress */}
          <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6">
            <ProgressBar
              status={job.status}
              progress={job.progress}
              errorMessage={job.error_message}
            />
          </div>

          {/* Schema Editor */}
          {(job.status === "SCHEMA_PROPOSED" ||
            job.status === "AWAITING_REVIEW") &&
            job.proposed_schema && (
              <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6">
                <SchemaForm
                  proposedSchema={job.proposed_schema}
                  onApprove={handleApproveSchema}
                  onReject={handleRejectSchema}
                  isSubmitting={isSubmitting}
                />
              </div>
            )}

          {/* Connection String */}
          {job.status === "COMPLETED" && job.connection_string && (
            <ConnectionString
              connectionString={job.connection_string}
              provisionedRows={job.provisioned_rows}
              provisionedAt={job.provisioned_at}
            />
          )}

          {/* Data Preview */}
          {job.status === "COMPLETED" && token && (
            <DataPreview jobId={job.id} token={token} />
          )}

          {/* Job Metadata */}
          <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Details
            </h3>
            <dl className="mt-4 grid grid-cols-2 gap-6 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-zinc-500">Format</dt>
                <dd className="mt-1 font-medium text-zinc-200">
                  {job.output_format}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">File Size</dt>
                <dd className="mt-1 font-medium font-mono text-zinc-200">
                  {(job.file_size / 1024 / 1024).toFixed(2)} MB
                </dd>
              </div>
              {job.page_count && (
                <div>
                  <dt className="text-zinc-500">Pages</dt>
                  <dd className="mt-1 font-medium font-mono text-zinc-200">
                    {job.page_count}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-zinc-500">Created</dt>
                <dd className="mt-1 font-medium text-zinc-200">
                  {new Date(job.created_at).toLocaleString()}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </main>
    </div>
  );
}
