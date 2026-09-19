/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.SAPLING_API_URL || 'http://127.0.0.1:8000'}/:path*` }];
  },
};
export default nextConfig;
