/**
 * ParseGrid — Auth.js SessionProvider wrapper.
 * Wraps the app to enable useSession() in client components.
 */

"use client";

import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

export function AuthProvider({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
