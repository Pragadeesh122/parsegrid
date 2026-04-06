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
import { AppShell } from "@/components/app-shell";
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
  const {
    data: job,
    isLoading: loading,
    error: queryError,
  } = useJob(jobId, token ?? "");
  const approveMutation = useApproveSchema(token ?? "");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // SSE overlay — merges live progress into the cached query data
  const isProcessing =
    !!job &&
    job.status !== "COMPLETED" &&
    job.status !== "FAILED" &&
    job.status !== "SCHEMA_PROPOSED" &&
    job.status !== "AWAITING_REVIEW" &&
    job.status !== "AWAITING_QUERY";

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
              connection_string:
                data.connection_string ?? prev.connection_string,
            }
          : prev,
      );

      if (
        data.status === "COMPLETED" ||
        data.status === "FAILED" ||
        data.status === "AWAITING_QUERY" ||
        data.status === "SCHEMA_PROPOSED"
      ) {
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
      const API_BASE =
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
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
      <AppShell>
        <div className="flex min-h-[60vh] items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
        </div>
      </AppShell>
    );
  }

  if (error || !job) {
    return (
      <AppShell>
        <div className="px-6 py-12 lg:px-10">
          <div className="mx-auto max-w-md text-center space-y-4">
            <p className="text-red-400">{error || "Job not found"}</p>
            <Link
              href="/dashboard"
              className="text-sm text-zinc-400 hover:text-zinc-100"
            >
              Back to Dashboard
            </Link>
          </div>
        </div>
      </AppShell>
    );
  }

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
          <span className="max-w-[300px] truncate text-zinc-300">
            {job.filename}
          </span>
        </div>

        {/* Header */}
        <div className="mt-4">
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
            {job.filename}
          </h1>
          <p className="mt-0.5 text-xs font-mono text-zinc-600">{job.id}</p>
        </div>

        <div className="mt-8 max-w-5xl space-y-8">
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
                <dt className="text-zinc-500">Mode</dt>
                <dd className="mt-1 font-medium text-zinc-200">
                  {job.job_type === "TARGETED" ? "Targeted" : "Full"}
                </dd>
              </div>
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
      </div>
    </AppShell>
  );
}
