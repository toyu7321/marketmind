import type {NextConfig} from 'next';

const nextConfig: NextConfig = {
  output: process.env.MARKETMIND_STANDALONE === 'true' ? 'standalone' : undefined,
  async headers() {
    return [
      {source: '/sw.js', headers: [{key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate'}]},
      {source: '/manifest.webmanifest', headers: [{key: 'Cache-Control', value: 'public, max-age=3600'}]},
    ];
  },
};

export default nextConfig;
