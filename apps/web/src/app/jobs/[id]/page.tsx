// Server Component shell to handle dynamic segments and avoid blocking route warnings
import JobDetailClient from "./client";

export default async function JobDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <JobDetailClient jobId={id} />;
}
