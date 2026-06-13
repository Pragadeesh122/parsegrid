/**
 * ParseGrid — Shared app shell for authenticated routes.
 * Sidebar navigation + mobile hamburger + user section.
 */

"use client";

import {useState} from "react";
import Link from "next/link";
import {usePathname} from "next/navigation";
import {useSession, signOut} from "next-auth/react";
import {useJobs} from "@/hooks/use-jobs";

interface AppShellProps {
  children: React.ReactNode;
  token?: string | null;
}

// Status → sidebar dot. Awaiting-user states are amber, terminal-good emerald,
// failure red, everything else is in-flight (pulsing emerald).
const AWAITING_STATUSES = new Set([
  "MODEL_PROPOSED",
  "AWAITING_REVIEW",
  "AWAITING_QUERY",
  "AWAITING_APPEND_REVIEW",
]);

function jobDotClass(status: string): string {
  if (status === "FAILED") return "bg-red-500";
  if (status === "COMPLETED") return "bg-emerald-500";
  if (AWAITING_STATUSES.has(status)) return "bg-amber-500";
  return "bg-emerald-500 animate-pulse";
}

export function AppShell({children, token}: AppShellProps) {
  const pathname = usePathname();
  const {data: session} = useSession();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const {data: jobsData} = useJobs(token ?? "");
  const recentJobs = (jobsData?.jobs ?? []).slice(0, 10);

  const user = session?.user;
  const initials = user?.name
    ? user.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? "?";

  const navItems = [
    {
      href: "/dashboard",
      label: "Dashboard",
      icon: (
        <svg
          className='h-4 w-4'
          fill='none'
          viewBox='0 0 24 24'
          stroke='currentColor'
          strokeWidth={1.5}>
          <path
            strokeLinecap='round'
            strokeLinejoin='round'
            d='M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z'
          />
        </svg>
      ),
      isActive:
        pathname === "/dashboard" ||
        (pathname.startsWith("/jobs/") && pathname !== "/jobs/new"),
    },
    {
      href: "/jobs/new",
      label: "New Job",
      icon: (
        <svg
          className='h-4 w-4'
          fill='none'
          viewBox='0 0 24 24'
          stroke='currentColor'
          strokeWidth={1.5}>
          <path
            strokeLinecap='round'
            strokeLinejoin='round'
            d='M12 4.5v15m7.5-7.5h-15'
          />
        </svg>
      ),
      isActive: pathname === "/jobs/new",
    },
  ];

  return (
    <div className='min-h-dvh'>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className='fixed inset-0 z-40 bg-black/50 lg:hidden'
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-zinc-800/60 bg-zinc-950 transition-transform duration-200 ease-out lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}>
        {/* Logo */}
        <div className='flex h-14 shrink-0 items-center gap-2 border-b border-zinc-800/60 px-5'>
          <Link
            href='/dashboard'
            className='flex items-center gap-2'
            onClick={() => setSidebarOpen(false)}>
            <span className='h-2 w-2 rounded-full bg-emerald-500' />
            <span className='text-sm font-semibold tracking-tight text-zinc-100'>
              ParseGrid
            </span>
          </Link>
        </div>

        {/* Navigation */}
        <nav className='flex flex-1 flex-col overflow-hidden px-3 pt-4'>
          <div className='space-y-1'>
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setSidebarOpen(false)}
                className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all ${
                  item.isActive
                    ? "bg-zinc-800/60 text-zinc-100"
                    : "text-zinc-500 hover:bg-zinc-900/60 hover:text-zinc-300"
                }`}>
                <span
                  className={
                    item.isActive
                      ? "text-emerald-500"
                      : "text-zinc-600 transition-colors group-hover:text-zinc-400"
                  }>
                  {item.icon}
                </span>
                {item.label}
              </Link>
            ))}
          </div>

          {/* Recent jobs */}
          {recentJobs.length > 0 && (
            <div className='mt-6 flex min-h-0 flex-1 flex-col'>
              <p className='px-3 pb-2 text-xs font-medium uppercase tracking-wider text-zinc-600'>
                Recent
              </p>
              <div className='-mr-1 space-y-0.5 overflow-y-auto pr-1'>
                {recentJobs.map((job) => {
                  const name = job.documents[0]?.filename ?? "Dataset";
                  const isActive = pathname === `/jobs/${job.id}`;
                  return (
                    <Link
                      key={job.id}
                      href={`/jobs/${job.id}`}
                      onClick={() => setSidebarOpen(false)}
                      title={name}
                      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-all ${
                        isActive
                          ? "bg-zinc-800/60 text-zinc-100"
                          : "text-zinc-500 hover:bg-zinc-900/60 hover:text-zinc-300"
                      }`}>
                      <span
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${jobDotClass(job.status)}`}
                      />
                      <span className='truncate'>{name}</span>
                      {job.document_count > 1 && (
                        <span className='ml-auto shrink-0 font-mono text-xs text-zinc-600'>
                          +{job.document_count - 1}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          )}
        </nav>

        {/* User section */}
        <div className='shrink-0 border-t border-zinc-800/60 p-4'>
          <div className='flex items-center gap-3'>
            <div className='flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs font-medium text-zinc-400'>
              {initials}
            </div>
            <div className='min-w-0 flex-1'>
              {user?.name && (
                <p className='truncate text-sm font-medium text-zinc-200'>
                  {user.name}
                </p>
              )}
              <p className='truncate text-xs text-zinc-500'>{user?.email}</p>
            </div>
          </div>
          <button
            onClick={() => signOut({callbackUrl: "/login"})}
            className='mt-3 flex w-full items-center justify-center rounded-xl border border-zinc-800 py-2 text-xs text-zinc-500 transition-all hover:border-zinc-700 hover:text-zinc-300 active:scale-[0.98]'>
            Sign out
          </button>
        </div>
      </aside>

      {/* Main area */}
      <div className='lg:pl-60'>
        {/* Mobile top bar */}
        <header className='sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-zinc-800/60 bg-zinc-950/80 px-6 backdrop-blur-xl lg:hidden'>
          <button
            onClick={() => setSidebarOpen(true)}
            className='rounded-lg p-1 text-zinc-400 transition-colors hover:text-zinc-200'>
            <svg
              className='h-5 w-5'
              fill='none'
              viewBox='0 0 24 24'
              stroke='currentColor'
              strokeWidth={2}>
              <path
                strokeLinecap='round'
                strokeLinejoin='round'
                d='M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5'
              />
            </svg>
          </button>
          <Link href='/dashboard' className='flex items-center gap-2'>
            <span className='h-1.5 w-1.5 rounded-full bg-emerald-500' />
            <span className='text-sm font-semibold tracking-tight text-zinc-100'>
              ParseGrid
            </span>
          </Link>
        </header>

        {children}
      </div>
    </div>
  );
}
