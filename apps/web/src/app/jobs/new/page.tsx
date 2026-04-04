/**
 * ParseGrid — New job page (Server Component wrapper).
 * Dynamic auth wrapped in Suspense per Next.js 16 cacheComponents.
 */

import { Suspense } from "react";
import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { getServerToken } from "@/lib/api-client";
import { NewJobClient } from "./client";

async function NewJobContent() {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  const token = await getServerToken();

  return <NewJobClient token={token} />;
}

export default function NewJobPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-indigo-400" />
        </div>
      }
    >
      <NewJobContent />
    </Suspense>
  );
}
