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
        <div className="flex min-h-[100dvh] items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
        </div>
      }
    >
      <JobDetailContent paramsPromise={params} />
    </Suspense>
  );
}
