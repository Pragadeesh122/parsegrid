/**
 * ParseGrid — Dashboard client component.
 * Stats overview + job table, wrapped in shared AppShell.
 */

"use client";

import { useMemo } from "react";
import Link from "next/link";
import { StatusBadge } from "@/components/job-status/status-badge";
import { useJobs } from "@/hooks/use-jobs";
import { AppShell } from "@/components/app-shell";

interface DashboardClientProps {
  user: {
    id?: string;
    name?: string | null;
    email?: string | null;
  };
  token: string | null;
}

export function DashboardClient({ token }: DashboardClientProps) {
  const { data, isLoading: loading } = useJobs(token ?? "");
  const jobs = data?.jobs ?? [];

  const stats = useMemo(() => {
    const total = jobs.length;
    const completed = jobs.filter((j) => j.status === "COMPLETED").length;
    const failed = jobs.filter((j) => j.status === "FAILED").length;
    const active = total - completed - failed;
    return [
      { label: "Total Jobs", value: total, accent: "text-zinc-100" },
      { label: "Active", value: active, accent: "text-emerald-400" },
      { label: "Completed", value: completed, accent: "text-zinc-100" },
      { label: "Failed", value: failed, accent: failed > 0 ? "text-red-400" : "text-zinc-100" },
    ];
  }, [jobs]);

  return (
    <AppShell>
      <div className="px-6 py-8 lg:px-10">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
              Dashboard
            </h1>
            <p className="mt-0.5 text-sm text-zinc-500">
              Monitor and manage your extraction jobs.
            </p>
          </div>
          <Link
            href="/jobs/new"
            className="hidden items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] sm:inline-flex"
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
                d="M12 4.5v15m7.5-7.5h-15"
              />
            </svg>
            New Job
          </Link>
        </div>

        {/* Stats */}
        {!loading && jobs.length > 0 && (
          <div className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-zinc-800/60 bg-zinc-800/30 lg:grid-cols-4">
            {stats.map((stat) => (
              <div key={stat.label} className="bg-zinc-950 p-5">
                <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                  {stat.label}
                </p>
                <p
                  className={`mt-2 text-2xl font-semibold font-mono ${stat.accent}`}
                >
                  {stat.value}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Jobs list */}
        <div className="mt-8">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Recent Jobs
          </h2>

          <div className="mt-3">
            {loading ? (
              <div className="flex items-center justify-center py-24">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
              </div>
            ) : jobs.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-zinc-800 py-20">
                <svg
                  className="h-10 w-10 text-zinc-700"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
                  />
                </svg>
                <p className="mt-4 text-sm text-zinc-500">
                  No extraction jobs yet.
                </p>
                <Link
                  href="/jobs/new"
                  className="mt-3 text-sm text-emerald-500 transition-colors hover:text-emerald-400"
                >
                  Upload your first document
                </Link>
              </div>
            ) : (
              <div className="overflow-hidden rounded-2xl border border-zinc-800/60">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-zinc-800/60 text-left text-xs font-medium uppercase tracking-wider text-zinc-500">
                      <th className="px-5 py-3">Document</th>
                      <th className="px-5 py-3">Status</th>
                      <th className="px-5 py-3 hidden sm:table-cell">
                        Progress
                      </th>
                      <th className="px-5 py-3 hidden md:table-cell">
                        Created
                      </th>
                      <th className="px-5 py-3 w-12" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {jobs.map((job) => (
                      <tr
                        key={job.id}
                        className="transition-colors hover:bg-zinc-900/40"
                      >
                        <td className="px-5 py-3.5">
                          <Link
                            href={`/jobs/${job.id}`}
                            className="group flex items-center gap-3"
                          >
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-800/60">
                              <svg
                                className="h-3.5 w-3.5 text-zinc-400"
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
                            <span className="text-sm font-medium text-zinc-200 transition-colors group-hover:text-zinc-100">
                              {job.filename}
                            </span>
                          </Link>
                        </td>
                        <td className="px-5 py-3.5">
                          <StatusBadge status={job.status} />
                        </td>
                        <td className="px-5 py-3.5 hidden sm:table-cell">
                          <div className="flex items-center gap-3">
                            <div className="h-1 w-24 rounded-full bg-zinc-800">
                              <div
                                className={`h-full rounded-full transition-all duration-500 ${
                                  job.status === "FAILED"
                                    ? "bg-red-500"
                                    : "bg-emerald-500"
                                }`}
                                style={{
                                  width: `${Math.min(job.progress, 100)}%`,
                                }}
                              />
                            </div>
                            <span className="text-xs font-mono text-zinc-500">
                              {Math.round(job.progress)}%
                            </span>
                          </div>
                        </td>
                        <td className="px-5 py-3.5 hidden md:table-cell">
                          <span className="text-sm text-zinc-500">
                            {new Date(job.created_at).toLocaleDateString(
                              undefined,
                              {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                          </span>
                        </td>
                        <td className="px-5 py-3.5">
                          <Link
                            href={`/jobs/${job.id}`}
                            className="text-zinc-600 transition-colors hover:text-zinc-300"
                          >
                            <svg
                              className="h-4 w-4"
                              fill="none"
                              viewBox="0 0 24 24"
                              stroke="currentColor"
                              strokeWidth={1.5}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M8.25 4.5l7.5 7.5-7.5 7.5"
                              />
                            </svg>
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
