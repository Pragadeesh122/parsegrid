import type { NextConfig } from "next";

const API_PROXY_BASE =
  process.env.INTERNAL_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

const nextConfig: NextConfig = {
  // Enable "use cache" directive for explicit opt-in caching
  cacheComponents: true,

  // Proxy API requests to FastAPI backend during development
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${API_PROXY_BASE}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
