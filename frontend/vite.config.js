import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'fs'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'serve-local-data',
      configureServer(server) {
        server.middlewares.use('/api/latest.json', (req, res) => {
          const filePath = path.resolve(__dirname, '../data/results/latest.json');
          if (fs.existsSync(filePath)) {
            res.setHeader('Content-Type', 'application/json');
            res.end(fs.readFileSync(filePath));
          } else {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'File not found' }));
          }
        });
        server.middlewares.use('/api/universe.json', (req, res) => {
          const filePath = path.resolve(__dirname, '../data/results/universe.json');
          if (fs.existsSync(filePath)) {
            res.setHeader('Content-Type', 'application/json');
            res.end(fs.readFileSync(filePath));
          } else {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'File not found' }));
          }
        });
      }
    }
  ],
})
