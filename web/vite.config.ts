import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://localhost:8000',
      '/auth/login': 'http://localhost:8000',
      '/auth/github': 'http://localhost:8000',
      '/auth/me': 'http://localhost:8000',
      '/repos': 'http://localhost:8000',
      '/webhooks': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
});
