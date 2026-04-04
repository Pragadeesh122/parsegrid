/**
 * ParseGrid — Next.js Edge Middleware for route protection.
 *
 * Protects /dashboard and /jobs routes.
 * Allows /login, /register, and /api/auth/* (NextAuth internal routes) to pass.
 */

export { auth as middleware } from "@/auth";

export const config = {
  matcher: ["/dashboard/:path*", "/jobs/:path*"],
};
