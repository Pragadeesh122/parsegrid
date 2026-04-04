/**
 * ParseGrid — Dashboard page (Server Component).
 * Wraps dynamic auth content in <Suspense> per Next.js 16 cacheComponents rules.
 */

import { Suspense } from "react";
import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { getServerToken } from "@/lib/api-client";
import { DashboardClient } from "./client";

async function DashboardContent() {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  const token = await getServerToken();

  return <DashboardClient user={session.user} token={token} />;
}

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-indigo-400" />
        </div>
      }
    >
      <DashboardContent />
    </Suspense>
  );
}
