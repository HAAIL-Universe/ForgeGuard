import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* Bypass proxy for browser page navigations (Accept: text/html) so Vite
   serves index.html and React Router handles the route. API fetch() calls
   (Accept: application/json) still proxy to the backend. */
const apiProxy = {
  target: 'http://localhost:8000',
  bypass(req: { headers: { accept?: string } }) {
    if (req.headers.accept?.includes('text/html')) {
      return '/index.html';
    }
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://localhost:8000',
      '/auth/login': 'http://localhost:8000',
      '/auth/github': 'http://localhost:8000',
      '/auth/me': 'http://localhost:8000',
      '/auth/api-key': 'http://localhost:8000',
      '/repos': apiProxy,
      '/projects': apiProxy,
      '/webhooks': 'http://localhost:8000',
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
});
