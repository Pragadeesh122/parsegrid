/**
 * ParseGrid — Landing page.
 */

"use cache";

import Link from "next/link";

export default async function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center px-6">
      {/* Hero */}
      <div className="max-w-3xl text-center space-y-8">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900/80 px-4 py-1.5 text-sm text-zinc-400 backdrop-blur">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          Open-Core • Local-First
        </div>

        <h1 className="text-5xl font-bold tracking-tight sm:text-6xl">
          <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
            ParseGrid
          </span>
        </h1>

        <p className="text-xl text-zinc-400 leading-relaxed">
          Convert messy, unstructured documents into structured, queryable
          databases — powered by{" "}
          <span className="text-zinc-200 font-medium">local OCR</span> and{" "}
          <span className="text-zinc-200 font-medium">AI extraction</span>.
        </p>

        {/* Features */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-left mt-12">
          {[
            {
              icon: "📄",
              title: "Upload Any Document",
              desc: "PDF, scanned images, complex layouts — ParseGrid handles it all.",
            },
            {
              icon: "🧠",
              title: "AI Schema Discovery",
              desc: "Our AI proposes a schema, you review and approve it.",
            },
            {
              icon: "🔗",
              title: "Get a Connection String",
              desc: "Your data lands in PostgreSQL. Connect with any tool.",
            },
          ].map((feature) => (
            <div
              key={feature.title}
              className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5 space-y-2 hover:border-zinc-700 transition-colors"
            >
              <span className="text-2xl">{feature.icon}</span>
              <h3 className="font-semibold text-zinc-200">{feature.title}</h3>
              <p className="text-sm text-zinc-500">{feature.desc}</p>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mt-8">
          <Link
            href="/jobs/new"
            className="rounded-xl bg-indigo-600 px-8 py-3 text-sm font-semibold text-white hover:bg-indigo-500 transition-colors shadow-lg shadow-indigo-500/20"
          >
            Upload a Document →
          </Link>
          <Link
            href="/dashboard"
            className="rounded-xl border border-zinc-700 px-8 py-3 text-sm font-medium text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            View Dashboard
          </Link>
        </div>
      </div>

      {/* Footer */}
      <footer className="mt-24 pb-8 text-center text-xs text-zinc-600">
        ParseGrid Community Edition • Built with PaddleOCR + OpenAI
      </footer>
    </main>
  );
}
