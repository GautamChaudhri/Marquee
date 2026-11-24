import { defineConfig, devices } from '@playwright/test';

/** End-to-end tests: the built app (`npm run build` first) served by `node build`, pointed
 *  at the fake backend in e2e/fake-backend.mjs. The key below exists only for these tests. */
export const E2E_API_KEY = 'e2e-only-key-not-a-secret';
const APP_PORT = 3198;
const FAKE_PORT = 3199;
export const APP_URL = `http://127.0.0.1:${APP_PORT}`;
export const FAKE_URL = `http://127.0.0.1:${FAKE_PORT}`;

export default defineConfig({
	testDir: 'e2e',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	timeout: 30_000,
	use: {
		baseURL: APP_URL,
		trace: 'retain-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: 'node e2e/fake-backend.mjs',
			url: `${FAKE_URL}/__fake/health`,
			env: { FAKE_API_PORT: String(FAKE_PORT), FAKE_API_KEY: E2E_API_KEY },
			reuseExistingServer: false,
			timeout: 15_000
		},
		{
			command: 'node build',
			url: `${APP_URL}/signin`,
			env: {
				HOST: '127.0.0.1',
				PORT: String(APP_PORT),
				ORIGIN: APP_URL,
				MARQUEE_API_URL: FAKE_URL,
				MARQUEE_API_KEY: E2E_API_KEY
			},
			reuseExistingServer: false,
			timeout: 30_000
		}
	]
});
