/**
 * ParseGrid — NextAuth API route handler.
 * Exposes /api/auth/* endpoints (signin, signout, session, csrf, etc.)
 */

import { handlers } from "@/auth";

export const { GET, POST } = handlers;
