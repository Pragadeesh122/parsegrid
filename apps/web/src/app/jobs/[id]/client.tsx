/**
 * ParseGrid — Job detail client component with real-time SSE status.
 *
 * Uses TanStack Query for data fetching + SSE overlay for live progress.
 * Falls back to query invalidation on terminal status to pick up fields
 * SSE doesn't carry (connection_string, etc.).
 */

"use client";

import { useState } from "react";
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/app-shell";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ProgressBar } from "@/components/job-status/progress-bar";
import { SchemaForm } from "@/components/schema-editor/schema-form";
import { ConnectionString } from "@/components/connection/conn-string";
import { DataPreview } from "@/components/data-preview/data-table";
import {
  useApproveSchema,
  useDeleteJob,
  useJob,
  useTargetQuery,
} from "@/hooks/use-jobs";
import { useSSE } from "@/hooks/use-sse";

export default function JobDetailClient({
  jobId,
  token,
}: {
  jobId: string;
  token: string | null;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const {
    data: job,
    isLoading: loading,
    error: queryError,
  } = useJob(jobId, token ?? "");
  const approveMutation = useApproveSchema(token ?? "");
  const deleteJobMutation = useDeleteJob(token ?? "");
  const targetQueryMutation = useTargetQuery(token ?? "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

  const handleTargetQuery = async () => {
    if (!targetQuery.trim()) return;

    try {
      await targetQueryMutation.mutateAsync({
        jobId,
        query: targetQuery,
      });
    } catch (e) {
      console.error("Target query failed:", e);
    }
  };

  const handleDeleteJob = async () => {
    if (!job) return;

    setDeleteError(null);
    setIsDeleting(true);
    try {
      await deleteJobMutation.mutateAsync(job.id);
      router.push("/dashboard");
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : "Failed to delete job",
      );
      setIsDeleting(false);
    }
  };

  const openDeleteDialog = () => {
    setDeleteError(null);
    setDeleteDialogOpen(true);
  };

  const closeDeleteDialog = () => {
    if (deleteJobMutation.isPending) return;
    setDeleteDialogOpen(false);
    setDeleteError(null);
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
        <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
              {job.filename}
            </h1>
            <p className="mt-0.5 text-xs font-mono text-zinc-600">{job.id}</p>
          </div>
          <button
            type="button"
            onClick={openDeleteDialog}
            disabled={isDeleting || deleteJobMutation.isPending}
            aria-label={`Delete ${job.filename}`}
            title="Delete job"
            className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-red-500/20 bg-red-500/10 text-red-300 transition-colors hover:border-red-500/30 hover:bg-red-500/15 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isDeleting ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-red-300 border-t-transparent" />
            ) : (
              <TrashIcon className="h-4 w-4" />
            )}
          </button>
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

          {/* Targeted Query Input */}
          {job.status === "AWAITING_QUERY" && (
            <div className="rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6">
              <h3 className="text-sm font-semibold text-zinc-200">
                Document Indexed
              </h3>
              <p className="mt-1 text-sm text-zinc-500">
                Your document has been indexed. What specific data would you
                like to extract?
              </p>
              <div className="mt-4 flex gap-3">
                <input
                  type="text"
                  value={targetQuery}
                  onChange={(e) => setTargetQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !targetQueryMutation.isPending)
                      handleTargetQuery();
                  }}
                  placeholder="e.g., Find all invoice totals and dates"
                  className="flex-1 rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none transition-colors focus:border-emerald-600"
                />
                <button
                  onClick={handleTargetQuery}
                  disabled={!targetQuery.trim() || targetQueryMutation.isPending}
                  className="rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {targetQueryMutation.isPending ? "Searching..." : "Extract"}
                </button>
              </div>
            </div>
          )}

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
      <ConfirmDialog
        open={deleteDialogOpen}
        title="Delete job permanently?"
        description={`"${job.filename}" will be removed along with its uploaded file, parsed artifacts, extracted data, and provisioned output. This cannot be undone.`}
        confirmLabel="Delete Job"
        confirmIcon={<TrashIcon className="h-5 w-5" weight="duotone" />}
        isPending={deleteJobMutation.isPending}
        errorMessage={deleteError}
        onConfirm={handleDeleteJob}
        onClose={closeDeleteDialog}
      />
    </AppShell>
  );
}
