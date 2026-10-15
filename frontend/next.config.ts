import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Set only when building the files the desktop app serves itself: the page is
  // plain client-side code, so it exports to static files in `out/`. A trailing
  // slash makes `/chat` the folder `chat/index.html`, which a plain file server
  // (the backend's) can serve; without it `/chat` would be a 404.
  ...(process.env.OD_STATIC_EXPORT === "1"
    ? { output: "export" as const, trailingSlash: true }
    : {}),
};

export default nextConfig;
