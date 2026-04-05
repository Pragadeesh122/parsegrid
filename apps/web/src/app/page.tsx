/**
 * ParseGrid — Landing page.
 * Asymmetric hero with left-aligned content + terminal visual.
 */

"use cache";

import Link from "next/link";

export default async function HomePage() {
  return (
    <div className="min-h-[100dvh] flex flex-col">
      {/* Nav */}
      <nav className="sticky top-0 z-30 border-b border-zinc-800/60 bg-zinc-950/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            <span className="text-base font-semibold tracking-tight text-zinc-100">
              ParseGrid
            </span>
          </Link>
          <div className="flex items-center gap-4">
            <Link
              href="/login"
              className="text-sm text-zinc-400 transition-colors hover:text-zinc-100"
            >
              Sign in
            </Link>
            <Link
              href="/jobs/new"
              className="rounded-xl bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98]"
            >
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero — asymmetric split */}
      <main className="flex flex-1 items-center">
        <div className="mx-auto grid w-full max-w-7xl grid-cols-1 gap-16 px-6 py-24 lg:grid-cols-2 lg:gap-20">
          {/* Left — copy */}
          <div className="flex flex-col justify-center space-y-8">
            <div className="inline-flex w-fit items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900/60 px-4 py-1.5 text-xs tracking-wide text-zinc-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              Open-Core &middot; Local-First
            </div>

            <h1 className="text-4xl font-bold leading-[1.1] tracking-tighter text-zinc-50 md:text-6xl">
              Documents in,
              <br />
              structured data out.
            </h1>

            <p className="max-w-[50ch] text-lg leading-relaxed text-zinc-400">
              Upload messy PDFs and scanned images. ParseGrid extracts
              structured, queryable data using local OCR and AI — then
              provisions it straight into your database.
            </p>

            <div className="flex items-center gap-4 pt-2">
              <Link
                href="/jobs/new"
                className="rounded-xl bg-emerald-600 px-7 py-3 text-sm font-semibold text-white transition-all hover:bg-emerald-500 active:scale-[0.98]"
              >
                Upload a Document
              </Link>
              <Link
                href="/dashboard"
                className="rounded-xl border border-zinc-800 px-7 py-3 text-sm font-medium text-zinc-300 transition-all hover:border-zinc-700 hover:bg-zinc-900 active:scale-[0.98]"
              >
                View Dashboard
              </Link>
            </div>
          </div>

          {/* Right — terminal pipeline visual */}
          <div className="flex items-center justify-end">
            <div className="w-full max-w-lg overflow-hidden rounded-2xl border border-zinc-800/60 bg-zinc-900/40 shadow-2xl shadow-black/20">
              {/* Terminal chrome */}
              <div className="flex items-center gap-2 border-b border-zinc-800/60 px-5 py-3.5">
                <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                <span className="ml-4 text-xs text-zinc-600 font-mono">
                  parsegrid pipeline
                </span>
              </div>
              {/* Pipeline steps */}
              <div className="space-y-0 p-5 font-mono text-sm">
                <div className="flex items-center gap-3 py-2">
                  <span className="text-emerald-500">&#10003;</span>
                  <span className="text-zinc-300">Upload PDF</span>
                  <span className="ml-auto text-xs text-zinc-600">0.2s</span>
                </div>
                <div className="flex items-center gap-3 py-2">
                  <span className="text-emerald-500">&#10003;</span>
                  <span className="text-zinc-300">Smart OCR routing</span>
                  <span className="ml-auto text-xs text-zinc-600">1.4s</span>
                </div>
                <div className="flex items-center gap-3 py-2">
                  <span className="text-emerald-500">&#10003;</span>
                  <span className="text-zinc-300">AI schema discovery</span>
                  <span className="ml-auto text-xs text-zinc-600">3.1s</span>
                </div>
                <div className="flex items-center gap-3 py-2">
                  <span className="text-zinc-500">&#9679;</span>
                  <span className="text-zinc-400">Human review</span>
                </div>
                <div className="flex items-center gap-3 py-2">
                  <span className="text-emerald-500">&#10003;</span>
                  <span className="text-zinc-300">Structured extraction</span>
                  <span className="ml-auto text-xs text-zinc-600">8.7s</span>
                </div>
                <div className="flex items-center gap-3 py-2">
                  <span className="text-emerald-500">&#10003;</span>
                  <span className="text-zinc-300">Database provisioned</span>
                  <span className="ml-auto text-xs text-zinc-600">0.4s</span>
                </div>
                <div className="mt-4 border-t border-zinc-800/60 pt-4">
                  <code className="text-xs text-zinc-500">
                    <span className="text-emerald-600">$</span>{" "}
                    <span className="text-zinc-400">
                      psql postgresql://...parsegrid
                    </span>
                  </code>
                  <div className="mt-1 text-xs text-zinc-600">
                    19 rows &middot; 6 columns &middot; ready to query
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Features — zig-zag 2-row */}
      <section className="border-t border-zinc-800/60">
        <div className="mx-auto max-w-7xl px-6 py-24">
          <div className="grid grid-cols-1 gap-px rounded-2xl border border-zinc-800/60 bg-zinc-800/30 md:grid-cols-2">
            {[
              {
                title: "Upload any document",
                desc: "PDFs, scanned images, complex multi-column layouts. Drop it in and let the pipeline handle the rest.",
                icon: (
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                  </svg>
                ),
              },
              {
                title: "Smart OCR routing",
                desc: "Native digital text is extracted instantly. Scanned pages fall back to PaddleOCR. No wasted compute.",
                icon: (
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" />
                  </svg>
                ),
              },
              {
                title: "Human-in-the-loop schema",
                desc: "AI proposes the extraction schema. You review, edit fields, adjust types — then lock it for extraction.",
                icon: (
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                ),
              },
              {
                title: "Get a connection string",
                desc: "Your structured data lands in PostgreSQL. Connect with psql, DBeaver, or any tool you already use.",
                icon: (
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m9.86-2.802a4.5 4.5 0 00-1.242-7.244l4.5-4.5a4.5 4.5 0 016.364 6.364l-1.757 1.757" />
                  </svg>
                ),
              },
            ].map((f) => (
              <div
                key={f.title}
                className="flex gap-5 bg-zinc-950 p-8 transition-colors hover:bg-zinc-900/60"
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900 text-emerald-500">
                  {f.icon}
                </div>
                <div className="space-y-1.5">
                  <h3 className="text-sm font-semibold text-zinc-100">
                    {f.title}
                  </h3>
                  <p className="text-sm leading-relaxed text-zinc-500">
                    {f.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-zinc-800/60 py-8 text-center text-xs text-zinc-600">
        ParseGrid Community Edition
      </footer>
    </div>
  );
}
