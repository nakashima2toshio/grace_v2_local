/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// ローカル開発: /api を FastAPI（uvicorn backend.app.main:app --port 8000）へ中継する。
// SSE（/api/support/stream/*）もこのプロキシ経由で届く。
//
// target は必ず IPv4 の 127.0.0.1 を使う（localhost にしない）。
// Node 18+ は `localhost` を IPv6（::1）優先で名前解決する一方、uvicorn は
// 既定で 127.0.0.1（IPv4）のみに bind するため、`http://localhost:8000` だと
// プロキシが ::1:8000 へ繋ぎに行って ECONNREFUSED となり、/api/* が全滅する
// （UI は表示されるが業界プロファイル等の取得が失敗する）。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
