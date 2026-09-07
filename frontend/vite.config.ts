import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // The published read-only copy, built by `./breadcrumbs pages` and served by the
      // backend. Proxied so it sits at /readonly on the dev server too, rather
      // than only on the backend's own port: checking it should not mean
      // switching origins halfway through a session.
      '/readonly': 'http://127.0.0.1:8000',
    },
  },
})
