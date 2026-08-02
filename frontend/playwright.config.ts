import { defineConfig, devices } from '@playwright/test';

/**
 * Deterministic Chromium-only E2E configuration.
 *
 * Two managed servers, both hermetic (nothing touches operator media, the live
 * database, or the real *arr/TMDB services):
 *
 *   1. `e2e/support/synthetic-backend.mjs` — a tiny HTTP server returning
 *      synthetic canonical job/activity/system JSON on `:3199`.
 *   2. the built SvelteKit app (adapter-node) on `:4173`, with its API proxy
 *      pointed at the synthetic backend via `MARQUEE_API_URL`.
 *
 * Failure artifacts (trace + screenshot) are retained under `test-results/`,
 * which is git-ignored.
 */
// Override these in a focused local run to avoid reusing an unrelated server
// that is already bound to the default development ports.
const APP_PORT = Number(process.env.MARQUEE_E2E_APP_PORT ?? 4173);
const API_PORT = Number(process.env.MARQUEE_E2E_API_PORT ?? 3199);

export default defineConfig({
	testDir: 'e2e',
	testIgnore: 'real-activity-lifecycle.spec.ts',
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	retries: 0,
	workers: 1,
	reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
	outputDir: 'test-results',
	use: {
		baseURL: `http://127.0.0.1:${APP_PORT}`,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure',
		video: 'off'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: `node e2e/support/synthetic-backend.mjs`,
			port: API_PORT,
			reuseExistingServer: !process.env.CI,
			timeout: 30_000,
			env: { SYNTHETIC_BACKEND_PORT: String(API_PORT) }
		},
		{
			command: `npm run build && node build/index.js`,
			port: APP_PORT,
			reuseExistingServer: !process.env.CI,
			timeout: 180_000,
			env: {
				PORT: String(APP_PORT),
				HOST: '127.0.0.1',
				ORIGIN: `http://127.0.0.1:${APP_PORT}`,
				MARQUEE_E2E_FIXTURES: '1',
				MARQUEE_API_URL: `http://127.0.0.1:${API_PORT}`,
				MARQUEE_API_KEY: 'e2e-synthetic-key'
			}
		}
	]
});
