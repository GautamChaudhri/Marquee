import { defineConfig, devices } from '@playwright/test';
import baseConfig from './playwright.config';

export default defineConfig({
	...baseConfig,
	testMatch: '**/*.visual.spec.ts',
	testIgnore: [],
	expect: {
		toHaveScreenshot: {
			animations: 'disabled',
			caret: 'hide',
			threshold: 0.2,
			maxDiffPixelRatio: 0.002
		}
	},
	use: {
		...baseConfig.use,
		...devices['Desktop Chrome'],
		colorScheme: 'dark',
		reducedMotion: 'reduce',
		viewport: { width: 1440, height: 1000 }
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
