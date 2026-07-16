import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test.describe('shared activity card', () => {
	test('is keyboard operable and has no axe violations', async ({ page }, testInfo) => {
		await page.goto('/__fixtures/activity-card');
		const expanded = page.getByRole('region', { name: 'Expanded card fixture' });
		await expect(expanded.getByRole('heading', { name: 'Dune: Part Two' })).toBeVisible();

		await expanded.getByRole('link', { name: 'Activity' }).focus();
		await page.keyboard.press('Tab');
		await expect(expanded.getByRole('link', { name: 'Details' })).toBeFocused();
		await expanded.getByRole('button', { name: 'Cancel' }).focus();
		await page.keyboard.press('Enter');
		await expect(page.getByRole('status')).toHaveText('Cancel request sent');

		const results = await new AxeBuilder({ page })
			.include('section[aria-label="Expanded card fixture"]')
			.analyze();
		await testInfo.attach('activity-card-axe.json', {
			body: JSON.stringify(results.violations, null, 2),
			contentType: 'application/json'
		});
		expect(results.violations).toEqual([]);
	});

	test('keeps both variants inside a narrow mobile viewport', async ({ page }) => {
		await page.setViewportSize({ width: 360, height: 800 });
		await page.goto('/__fixtures/activity-card');
		for (const card of await page.locator('article').all()) {
			const bounds = await card.boundingBox();
			expect(bounds).not.toBeNull();
			expect(bounds!.x).toBeGreaterThanOrEqual(0);
			expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(360);
		}
		await expect(page.getByRole('link', { name: 'Activity' }).first()).toBeVisible();
		await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
	});
});
