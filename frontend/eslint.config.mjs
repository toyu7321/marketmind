import { FlatCompat } from '@eslint/eslintrc';

const compat = new FlatCompat({baseDirectory: import.meta.dirname});
const config = [
  {
    // Build artifacts contain generated CommonJS glue and must never be
    // treated as authored source by a repository-wide lint invocation.
    ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    files: ['**/*.{ts,tsx}'],
    rules: {
      // API payloads are intentionally normalized at provider boundaries and
      // rendered by compact presentation components.
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
];

export default config;
