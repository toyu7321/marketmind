import type {NextConfig} from 'next';

const backend = process.env.BACKEND_URL || 'http://localhost:8000';
const nextConfig: NextConfig = {
  output: process.env.MARKETMIND_STANDALONE === 'true' ? 'standalone' : undefined,
  async rewrites() {
    return [{source: '/api/:path*', destination: backend + '/api/:path*'}];
  },
};

export default nextConfig;
