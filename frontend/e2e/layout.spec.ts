import { expect, test, type Page } from '@playwright/test';
import { resetBackend, signIn } from './helpers';

async function overflowsSideways(page: Page): Promise<boolean> {
	return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
}

for (const width of [390, 1440]) {
	test(`the pages stay usable at ${width} px wide`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await resetBackend();
		await signIn(page);

		await expect(page.getByTestId('movie-tile').first()).toBeVisible();
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Sync now' })).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
		const columns = await page
			.getByTestId('library-grid')
			.evaluate((grid) => getComputedStyle(grid).gridTemplateColumns.split(' ').length);
		expect(columns).toBeGreaterThanOrEqual(width < 500 ? 3 : 6);

		await page.goto('/movies/1');
		await expect(page.getByTestId('candidates')).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);

		await page.goto('/activity?job=31');
		await expect(page.getByTestId('job-detail')).toBeVisible();
		await expect(page.getByTestId('job-row').first()).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
	});
}
