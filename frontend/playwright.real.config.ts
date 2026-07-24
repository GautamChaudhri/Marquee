import { defineConfig, devices } from '@playwright/test';

/**
 * JMC7C's lifecycle project deliberately has no synthetic backend or route
 * interception. Its API process resets and seeds the owned disposable database,
 * starts FastAPI's embedded PgQueuer worker, and exposes the real SSE tailer.
 */
const APP_PORT = 4201;
const API_PORT = 3201;
const DB_URL = 'postgresql+asyncpg://marquee@localhost:55458/marquee_test';
const DATA_DIR = '/tmp/marquee-jmc7c-browser-data';

export default defineConfig({
	testDir: 'e2e',
	testMatch: 'real-activity-lifecycle.spec.ts',
	fullyParallel: false,
	workers: 1,
	retries: 0,
	reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-real-report' }]],
	outputDir: 'test-results-real',
	use: {
		baseURL: `http://127.0.0.1:${APP_PORT}`,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure',
		video: 'off'
	},
	projects: [{ name: 'chromium-real', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: 'rtk .venv/bin/python -m tests.support.jmc7c_real_browser_server',
			cwd: '..',
			port: API_PORT,
			reuseExistingServer: false,
			timeout: 90_000,
			env: {
				DB_URL,
				DATA_DIR,
				DEBUG: 'true',
				MARQUEE_ENVIRONMENT: 'test',
				MARQUEE_PROCESS_ROLE: 'api',
				JOB_EMBEDDED_WORKERS: 'true',
				MEDIA_ROOTS: JSON.stringify([DATA_DIR]),
				RADARR_PATH_PREFIX: '/jmc7c',
				RADARR_MEDIA_PATH: `${DATA_DIR}/jmc7c-real-browser`,
				RADARR_URL: '',
				RADARR_API_KEY: '',
				SONARR_URL: '',
				SONARR_API_KEY: '',
				TMDB_READ_ACCESS_TOKEN: '',
				JMC7C_API_PORT: String(API_PORT)
			}
		},
		{
			command: 'npm run build && node build/index.js',
			port: APP_PORT,
			reuseExistingServer: false,
			timeout: 180_000,
			env: {
				PORT: String(APP_PORT),
				HOST: '127.0.0.1',
				ORIGIN: `http://127.0.0.1:${APP_PORT}`,
				MARQUEE_API_URL: `http://127.0.0.1:${API_PORT}`,
				MARQUEE_E2E_FIXTURES: '1'
			}
		}
	]
});
