import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { projectServer } from './project-server.mjs';

export default defineConfig(({ mode }) => ({
  base: mode === 'project' ? '/' : '/internal/v1/dashboard/',
  build: { outDir: mode === 'project' ? 'project-dist' : 'dist', manifest: 'asset-manifest.json', sourcemap: false },
  plugins: [react(), ...(mode === 'project' ? [projectServer()] : [])],
  server: { host: '127.0.0.1', cors: false, fs: { strict: true, allow: ['.'] } },
  preview: { host: '127.0.0.1', cors: false },
}));
