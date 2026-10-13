import { defineConfig } from '@playwright/test';
import { join } from 'node:path';
const scratch = process.env.PI_SCRATCH_DIR;
// Default stays the bundled Chromium. PI_PLAYWRIGHT_CHANNEL=msedge runs the same
// suites on the installed Edge build, which is the engine WebView2 actually uses.
const channel = process.env.PI_PLAYWRIGHT_CHANNEL;
export default defineConfig({
  testDir: './tests/browser', timeout: 45000, workers: 1,
  outputDir: scratch ? join(scratch,'frontend-playwright-results') : '../../test-records/frontend/playwright-results',
  reporter: 'list', use: {...(channel ? {channel} : {}), baseURL: 'http://127.0.0.1:5178', viewport: {width:1280,height:850}, screenshot:'only-on-failure', trace:'retain-on-failure'},
  webServer: {command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5178 --strictPort',url:'http://127.0.0.1:5178',reuseExistingServer:false,timeout:60000},
});
