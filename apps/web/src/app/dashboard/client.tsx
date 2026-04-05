/**
 * ParseGrid — Dashboard client component.
 * Uses TanStack Query for automatic polling of job status.
 */

"use client";

import Link from "next/link";
import { signOut } from "next-auth/react";
import { StatusBadge } from "@/components/job-status/status-badge";
import { useJobs } from "@/hooks/use-jobs";

interface DashboardClientProps {
  user: {
    id?: string;
    name?: string | null;
    email?: string | null;
  };
  token: string | null;
}

export function DashboardClient({ user, token }: DashboardClientProps) {
  const { data, isLoading: loading } = useJobs(token ?? "");
  const jobs = data?.jobs ?? [];

  return (
    <div className="min-h-[100dvh] flex flex-col">
      {/* Nav */}
      <nav className="sticky top-0 z-30 border-b border-zinc-800/60 bg-zinc-950/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <Link href="/dashboard" className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            <span className="text-base font-semibold tracking-tight text-zinc-100">
              ParseGrid
            </span>
          </Link>
          <div className="flex items-center gap-3">
            <span className="text-sm text-zinc-500">
              {user.name || user.email}
            </span>
            <Link
              href="/jobs/new"
              className="rounded-xl bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98]"
            >
              New Job
            </Link>
            <button
              onClick={() => signOut({ callbackUrl: "/login" })}
              className="rounded-xl border border-zinc-800 px-4 py-2 text-sm text-zinc-400 transition-all hover:border-zinc-700 hover:text-zinc-200 active:scale-[0.98]"
            >
              Sign Out
            </button>
          </div>
        </div>
      </nav>

      {/* Content */}
      <main className="flex-1">
        <div className="mx-auto max-w-7xl px-6 py-10">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-100">
            Jobs
          </h1>

          <div className="mt-6">
            {loading ? (
              <div className="flex items-center justify-center py-24">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
              </div>
            ) : jobs.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-zinc-800 py-24">
                <svg
                  className="h-10 w-10 text-zinc-700"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
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
                      <th className="px-6 py-3.5">Document</th>
                      <th className="px-6 py-3.5">Status</th>
                      <th className="px-6 py-3.5">Progress</th>
                      <th className="px-6 py-3.5">Created</th>
                      <th className="px-6 py-3.5 w-20" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {jobs.map((job) => (
                      <tr
                        key={job.id}
                        className="transition-colors hover:bg-zinc-900/40"
                      >
                        <td className="px-6 py-4">
                          <span className="text-sm font-medium text-zinc-200">
                            {job.filename}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <StatusBadge status={job.status} />
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-3">
                            <div className="h-1 w-28 rounded-full bg-zinc-800">
                              <div
                                className="h-full rounded-full bg-emerald-500 transition-all duration-500"
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
                        <td className="px-6 py-4 text-sm text-zinc-500">
                          {new Date(job.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-6 py-4">
                          <Link
                            href={`/jobs/${job.id}`}
                            className="text-sm text-zinc-400 transition-colors hover:text-zinc-100"
                          >
                            View
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
      </main>
    </div>
  );
}
