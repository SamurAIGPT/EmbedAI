import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Set only when building the files the desktop app serves itself: the page
  // is plain client-side code, so it exports to static files in `out/`.
  ...(process.env.OD_STATIC_EXPORT === "1" ? { output: "export" as const } : {}),
};

export default nextConfig;
