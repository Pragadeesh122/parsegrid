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
        <div className="flex min-h-[100dvh] items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
        </div>
      }
    >
      <DashboardContent />
    </Suspense>
  );
}
