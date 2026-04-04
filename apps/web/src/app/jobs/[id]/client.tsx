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
      // Merge SSE status into TanStack Query cache
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

      // On terminal status, invalidate to get all fields (provisioned_rows, etc.)
      if (data.status === "COMPLETED" || data.status === "FAILED") {
        queryClient.invalidateQueries({ queryKey: ["job", jobId] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }
    },
  });

  // --- Schema approval ---
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

  // --- Schema rejection ---
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

  // --- Render states ---

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-12 text-center space-y-4">
        <p className="text-red-400">{error || "Job not found"}</p>
        <Link
          href="/dashboard"
          className="text-sm text-indigo-400 hover:text-indigo-300"
        >
          &larr; Back to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-12 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link
            href="/dashboard"
            className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            &larr; Dashboard
          </Link>
          <h1 className="mt-2 text-2xl font-bold text-zinc-100">
            {job.filename}
          </h1>
          <p className="text-sm text-zinc-500 font-mono">{job.id}</p>
        </div>
      </div>

      {/* Progress */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <ProgressBar
          status={job.status}
          progress={job.progress}
          errorMessage={job.error_message}
        />
      </div>

      {/* Schema Editor (shows when schema is proposed) */}
      {(job.status === "SCHEMA_PROPOSED" ||
        job.status === "AWAITING_REVIEW") &&
        job.proposed_schema && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
            <SchemaForm
              proposedSchema={job.proposed_schema}
              onApprove={handleApproveSchema}
              onReject={handleRejectSchema}
              isSubmitting={isSubmitting}
            />
          </div>
        )}

      {/* Connection String (shows when completed) */}
      {job.status === "COMPLETED" && job.connection_string && (
        <ConnectionString
          connectionString={job.connection_string}
          provisionedRows={job.provisioned_rows}
          provisionedAt={job.provisioned_at}
        />
      )}

      {/* Data Preview (shows when completed) */}
      {job.status === "COMPLETED" && token && (
        <DataPreview jobId={job.id} token={token} />
      )}

      {/* Job Metadata */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 space-y-3">
        <h3 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
          Job Details
        </h3>
        <dl className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-zinc-500">Output Format</dt>
            <dd className="text-zinc-200 font-medium">{job.output_format}</dd>
          </div>
          <div>
            <dt className="text-zinc-500">File Size</dt>
            <dd className="text-zinc-200 font-medium">
              {(job.file_size / 1024 / 1024).toFixed(2)} MB
            </dd>
          </div>
          {job.page_count && (
            <div>
              <dt className="text-zinc-500">Pages</dt>
              <dd className="text-zinc-200 font-medium">{job.page_count}</dd>
            </div>
          )}
          <div>
            <dt className="text-zinc-500">Created</dt>
            <dd className="text-zinc-200 font-medium">
              {new Date(job.created_at).toLocaleString()}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
