import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'
import { analyzePlugin } from './server/analyze.ts'

export default defineConfig(({ mode }) => {
  // '' prefix loads non-VITE_ vars too; they stay in the dev server and never reach the bundle.
  const env = loadEnv(mode, process.cwd(), '')
  return {
    // DEMO_BASE=/demo/ builds the mock dashboard that ships inside landing/demo.
    base: process.env.DEMO_BASE || '/',
    plugins: [react(), tailwindcss(), analyzePlugin(env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY)],
    server: { port: 5173 },
  }
})
