/**
 * ParseGrid — Auth.js (NextAuth v5) configuration.
 *
 * CRITICAL DESIGN DECISIONS:
 * 1. CredentialsProvider — validates via HTTP POST to FastAPI, NOT direct DB access.
 * 2. GitHub + Google OAuth — upserts users via FastAPI /oauth-upsert endpoint.
 * 3. Custom JWT encode/decode — overrides JWE encryption to produce HS256 JWS tokens
 *    that FastAPI can verify with PyJWT using the shared AUTH_SECRET.
 * 4. The `sub` claim carries the user_id — this is what FastAPI reads.
 */

import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { SignJWT, jwtVerify } from "jose";

const API_BASE =
  process.env.INTERNAL_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

const secret = new TextEncoder().encode(process.env.AUTH_SECRET!);

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      name: "ParseGrid",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;

        try {
          const res = await fetch(
            `${API_BASE}/api/v1/auth/verify-credentials`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                email: credentials.email,
                password: credentials.password,
              }),
            },
          );

          if (!res.ok) return null;

          const user = await res.json();
          return { id: user.id, email: user.email, name: user.name };
        } catch {
          return null;
        }
      },
    }),
    GitHub({
      clientId: process.env.GITHUB_ID,
      clientSecret: process.env.GITHUB_SECRET,
    }),
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 24 * 60 * 60,
  },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async signIn({ user, account }) {
      // For OAuth providers, upsert the user in FastAPI's database
      if (account?.provider && account.provider !== "credentials") {
        try {
          const res = await fetch(`${API_BASE}/api/v1/auth/oauth-upsert`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              email: user.email,
              name: user.name,
              auth_provider: account.provider,
            }),
          });

          if (!res.ok) return false;

          // Replace the OAuth provider's random ID with our DB user_id
          const dbUser = await res.json();
          user.id = dbUser.id;
        } catch {
          return false;
        }
      }
      return true;
    },
    async jwt({ token, user }) {
      if (user) {
        token.sub = user.id;
        token.email = user.email;
        token.name = user.name;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user && token.sub) {
        session.user.id = token.sub;
      }
      return session;
    },
  },
  jwt: {
    encode: async ({ token }) => {
      if (!token) return "";

      const jwt = await new SignJWT(token as Record<string, unknown>)
        .setProtectedHeader({ alg: "HS256" })
        .setIssuedAt()
        .setExpirationTime("24h")
        .sign(secret);

      return jwt;
    },
    decode: async ({ token: tokenStr }) => {
      if (!tokenStr) return null;

      try {
        const { payload } = await jwtVerify(tokenStr, secret, {
          algorithms: ["HS256"],
        });
        return payload as Record<string, unknown>;
      } catch {
        return null;
      }
    },
  },
});

