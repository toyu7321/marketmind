import type {NextConfig} from 'next';

const configuredBackend = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_BASE_URL;
const backend = (configuredBackend || (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : undefined))?.replace(/\/?api\/?$/, '');
const nextConfig: NextConfig = {
  output: process.env.MARKETMIND_STANDALONE === 'true' ? 'standalone' : undefined,
  async headers() {
    return [{source: '/sw.js', headers: [{key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate'}]}];
  },
  async rewrites() {
    return backend ? [{source: '/api/:path*', destination: backend + '/api/:path*'}] : [];
  },
};

export default nextConfig;
