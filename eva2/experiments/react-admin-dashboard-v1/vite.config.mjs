import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/internal/v1/dashboard/',
  build: { manifest: 'asset-manifest.json', sourcemap: false },
  plugins: [react()],
});
