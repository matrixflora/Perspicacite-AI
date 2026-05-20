import type { NextConfig } from "next";

const BACKEND =
  process.env.PERSPICACITE_BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Move the Next.js dev-mode indicator out of the sidebar's bottom-left
  // corner so it doesn't overlap the CNRS / UniCA / 3iA logos and the
  // GitHub link. Has no effect on production builds (next build / start).
  devIndicators: {
    position: "bottom-right",
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
    ];
  },
};

export default nextConfig;
