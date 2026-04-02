/**
 * ParseGrid — Dashboard page.
 * Shows all jobs for the current user with status badges.
 */

"use client";

import Link from "next/link";
import { StatusBadge } from "@/components/job-status/status-badge";

// Placeholder: In production, this would use Auth.js session token
const MOCK_TOKEN = "dev-token";

export default function DashboardPage() {
  // Using TanStack Query for job list
  // const { data, isLoading } = useJobs(MOCK_TOKEN);

  return (
    <div className="mx-auto max-w-5xl px-6 py-12 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Dashboard</h1>
          <p className="text-sm text-zinc-500">
            Your extraction jobs and their status.
          </p>
        </div>
        <Link
          href="/jobs/new"
          className="rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
        >
          + New Job
        </Link>
      </div>

      {/* Jobs List (placeholder until Auth is wired) */}
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
            {/* Empty state */}
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
          </tbody>
        </table>
      </div>
    </div>
  );
}
