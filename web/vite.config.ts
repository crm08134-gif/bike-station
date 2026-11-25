import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const page = (name: string) => fileURLToPath(new URL(name, import.meta.url));

export default defineConfig({
  // MapLibre는 모듈 워커를 띄운다. 워커는 src/map.ts에서 ?worker&url로 따로 묶는다
  worker: { format: 'es' },
  build: {
    // MapLibre GL 자체가 약 1MB(gzip 약 280KB)라 기본 경고 기준(500KB)을 넘는다
    chunkSizeWarningLimit: 1200,
    rolldownOptions: {
      input: { main: page('index.html'), limits: page('limits.html') },
    },
  },
  // Spring API의 CORS 기본 허용 주소 (bikestation.cors.allowed-origins)와 맞춘다
  server: { port: 5173, strictPort: true },
});
