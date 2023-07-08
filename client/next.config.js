/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    dirs: [], // Only run ESLint these directories during production builds (next build)
  },
}

module.exports = nextConfig
