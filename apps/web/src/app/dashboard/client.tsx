/**
 * ParseGrid — Dashboard client component.
 */

"use client";

import Link from "next/link";
import { signOut } from "next-auth/react";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/job-status/status-badge";
import type { Job } from "@/lib/api-client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DashboardClientProps {
  user: {
    id?: string;
    name?: string | null;
    email?: string | null;
  };
  token: string | null;
}

export function DashboardClient({ user, token }: DashboardClientProps) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;

    const fetchJobs = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/jobs`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setJobs(data.jobs || []);
        }
      } catch {
        // Silently fail — empty state is shown
      } finally {
        setLoading(false);
      }
    };

    fetchJobs();
    // Poll every 10 seconds
    const interval = setInterval(fetchJobs, 10000);
    return () => clearInterval(interval);
  }, [token]);

  return (
    <div className="mx-auto max-w-5xl px-6 py-12 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Dashboard</h1>
          <p className="text-sm text-zinc-500">
            Welcome, {user.name || user.email}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/jobs/new"
            className="rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
          >
            + New Job
          </Link>
          <button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="rounded-xl border border-zinc-700 px-4 py-2.5 text-sm font-medium text-zinc-400 hover:border-zinc-600 hover:text-zinc-200 transition-colors"
          >
            Sign Out
          </button>
        </div>
      </div>

      {/* Jobs List */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs font-medium uppercase tracking-wider text-zinc-500">
              <th className="px-6 py-4">Document</th>
              <th className="px-6 py-4">Status</th>
              <th className="px-6 py-4">Progress</th>
              <th className="px-6 py-4">Created</th>
              <th className="px-6 py-4"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={5} className="px-6 py-16 text-center">
                  <div className="flex items-center justify-center gap-3 text-zinc-500">
                    <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
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
                    Loading jobs...
                  </div>
                </td>
              </tr>
            ) : jobs.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-6 py-16 text-center">
                  <div className="space-y-3 text-zinc-500">
                    <svg
                      className="mx-auto h-12 w-12 text-zinc-700"
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
                    <p>No extraction jobs yet.</p>
                    <Link
                      href="/jobs/new"
                      className="inline-block text-sm text-indigo-400 hover:text-indigo-300"
                    >
                      Upload your first document →
                    </Link>
                  </div>
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr
                  key={job.id}
                  className="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30 transition-colors"
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
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 rounded-full bg-zinc-800">
                        <div
                          className="h-full rounded-full bg-indigo-500 transition-all"
                          style={{
                            width: `${Math.min(job.progress, 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-xs text-zinc-500 font-mono">
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
                      className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
                    >
                      View →
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
