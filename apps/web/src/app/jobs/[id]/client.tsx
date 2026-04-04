/**
 * ParseGrid — Job detail client component with real-time SSE status.
 */

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ProgressBar } from "@/components/job-status/progress-bar";
import { SchemaForm } from "@/components/schema-editor/schema-form";
import { ConnectionString } from "@/components/connection/conn-string";
import type { Job } from "@/lib/api-client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function JobDetailClient({
  jobId,
  token,
}: {
  jobId: string;
  token: string | null;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch job details
  useEffect(() => {
    const fetchJob = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error("Job not found");
        const data = await res.json();
        setJob(data);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    };
    fetchJob();
  }, [jobId]);

  // Poll for updates when job is in a processing state
  useEffect(() => {
    if (
      !job ||
      job.status === "COMPLETED" ||
      job.status === "FAILED" ||
      job.status === "SCHEMA_PROPOSED" ||
      job.status === "AWAITING_REVIEW"
    ) {
      return;
    }

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/status`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (res.ok) {
          const statusData = await res.json();
          setJob((prev) =>
            prev ? { ...prev, ...statusData } : prev,
          );
        }
      } catch {
        // Silently retry
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [job?.status, jobId]);

  // Schema approval handler
  const handleApproveSchema = async (
    editedSchema: Record<string, unknown>,
  ) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/jobs/${jobId}/approve-schema`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ locked_schema: editedSchema }),
        },
      );
      if (!res.ok) throw new Error("Schema approval failed");
      const updatedJob = await res.json();
      setJob(updatedJob);
    } catch (e) {
      console.error("Schema approval failed:", e);
    }
  };

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
          ← Back to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link
            href="/dashboard"
            className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            ← Dashboard
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
            />
          </div>
        )}

      {/* Connection String (shows when completed) */}
      {job.status === "COMPLETED" && job.connection_string && (
        <ConnectionString connectionString={job.connection_string} />
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
