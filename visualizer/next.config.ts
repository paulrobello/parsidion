import type { NextConfig } from "next";

// SEC-006: Security headers for the visualizer (local dev tool).
// X-Frame-Options and X-Content-Type-Options provide defense-in-depth
// even for a localhost-only server.  CSP is intentionally permissive
// because sigma.js renders via WebGL canvas and loads assets inline.
const securityHeaders = [
  { key: "X-Frame-Options",        value: "SAMEORIGIN" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy",        value: "strict-origin-when-cross-origin" },
]

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ]
  },
}

export default nextConfig;
