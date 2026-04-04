/**
 * ParseGrid — Job detail page (Server Component).
 * Dynamic auth wrapped in Suspense per Next.js 16 cacheComponents.
 */

import { Suspense } from "react";
import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { getServerToken } from "@/lib/api-client";
import JobDetailClient from "./client";

async function JobDetailContent({
  paramsPromise,
}: {
  paramsPromise: Promise<{ id: string }>;
}) {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  const { id } = await paramsPromise;
  const token = await getServerToken();

  return <JobDetailClient jobId={id} token={token} />;
}

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-indigo-400" />
        </div>
      }
    >
      <JobDetailContent paramsPromise={params} />
    </Suspense>
  );
}
