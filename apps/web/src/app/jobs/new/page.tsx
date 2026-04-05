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

  // key={Date.now()} forces a fresh component mount on every navigation,
  // resetting file selection and upload state.
  return <NewJobClient key={Date.now()} token={token} />;
}

export default function NewJobPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[100dvh] items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
        </div>
      }
    >
      <NewJobContent />
    </Suspense>
  );
}
