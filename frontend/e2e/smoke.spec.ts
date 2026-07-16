import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

// A1 infrastructure smoke: prove the Chromium + axe harness drives the built app
// against a synthetic backend. JMC6A's own components gate on zero axe
// violations in A4; this test does not assert the legacy page's a11y debt.
test.describe('activity shell smoke', () => {
	test('renders the app shell against the synthetic backend', async ({ page }) => {
		const response = await page.goto('/projection-room');
		expect(response?.ok()).toBe(true);
		await expect(page.locator('main')).toBeVisible();
	});

	test('runs an axe accessibility scan on the shell', async ({ page }, testInfo) => {
		await page.goto('/projection-room');
		await expect(page.locator('main')).toBeVisible();
		const results = await new AxeBuilder({ page }).analyze();
		expect(results.violations).toEqual([]);
		await testInfo.attach('axe-violations.json', {
			body: JSON.stringify(results.violations, null, 2),
			contentType: 'application/json'
		});
	});
});
