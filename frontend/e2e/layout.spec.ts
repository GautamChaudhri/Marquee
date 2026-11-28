import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';
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

		await page.goto('/review');
		await expect(page.getByTestId('review-row').first()).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
		const queueColumns = await page
			.getByTestId('review-queue')
			.evaluate((list) => getComputedStyle(list).gridTemplateColumns.split(' ').length);
		expect(queueColumns).toBe(width < 500 ? 1 : 2);
		await page.getByRole('button', { name: 'Gallery' }).click();
		await expect(page.getByTestId('review-tile').first()).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
		const galleryColumns = await page
			.getByTestId('review-gallery')
			.evaluate((grid) => getComputedStyle(grid).gridTemplateColumns.split(' ').length);
		expect(galleryColumns).toBeGreaterThanOrEqual(width < 500 ? 2 : 6);
		await page.getByRole('button', { name: 'List' }).click();

		await page.goto('/review/12');
		await expect(page.getByTestId('review-card').first()).toBeVisible();
		await expect(page.getByRole('button', { name: 'Keep the top pick' })).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
		const cardColumns = await page
			.getByTestId('order-list')
			.evaluate((zone) => getComputedStyle(zone).gridTemplateColumns.split(' ').length);
		expect(cardColumns).toBeGreaterThanOrEqual(width < 500 ? 2 : 6);
		// A design's popup and the large view both stay inside the window.
		await page.getByTestId('review-card').first().getByTestId('open-design').click();
		const dialog = page.getByTestId('design-dialog');
		await expect(dialog.getByTestId('use-this-one')).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);
		const panel = (await dialog.boundingBox())!;
		expect(panel.x).toBeGreaterThanOrEqual(0);
		expect(panel.x + panel.width).toBeLessThanOrEqual(width);
		expect(panel.y + panel.height).toBeLessThanOrEqual(page.viewportSize()!.height);
		// Nothing in the popup is squeezed over anything else: the score details begin where
		// the posters end (below them on a phone, beside them on a desk).
		const variants = (await dialog.getByTestId('variants').boundingBox())!;
		const selected = (await dialog.getByTestId('selected-poster').boundingBox())!;
		const scores = (await dialog.getByTestId('design-scores').boundingBox())!;
		if (width < 500) {
			expect(selected.y + selected.height).toBeLessThanOrEqual(variants.y);
			expect(scores.y).toBeGreaterThanOrEqual(variants.y + variants.height);
		} else {
			expect(selected.x + selected.width).toBeLessThanOrEqual(variants.x);
			expect(scores.x + scores.width).toBeLessThanOrEqual(variants.x);
			expect(scores.y).toBeGreaterThanOrEqual(selected.y + selected.height);
		}
		await dialog.getByRole('button', { name: 'Look closer at variant 1', exact: true }).click();
		// The large copy (1000 by 1500 px) is whole, in a poster's shape, inside the window.
		const picture = page.getByTestId('lightbox-image');
		await expect(picture).toHaveJSProperty('naturalHeight', 1500);
		await page
			.getByTestId('lightbox')
			.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
		const large = (await picture.boundingBox())!;
		expect(large.x).toBeGreaterThanOrEqual(0);
		expect(large.x + large.width).toBeLessThanOrEqual(width);
		expect(large.y).toBeGreaterThanOrEqual(0);
		expect(large.y + large.height).toBeLessThanOrEqual(page.viewportSize()!.height);
		expect(large.width / large.height).toBeCloseTo(2 / 3, 1);
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		await expect(dialog).toHaveCount(0);
		await page.getByTestId('deploy-checkbox').uncheck();
		await page.getByRole('button', { name: 'Keep the top pick' }).click();
		await expect(page.getByTestId('answer-result')).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);

		await page.goto('/taste');
		await expect(page.getByTestId('exemplar').first()).toBeVisible();
		await expect(page.getByTestId('recent-feedback')).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);

		await page.goto('/system');
		await expect(page.getByTestId('models')).toBeVisible();
		expect(await overflowsSideways(page)).toBe(false);

		// Every page is in the navigation, and at this width it fits.
		const nav = page.getByRole('navigation', { name: 'Main' });
		for (const name of ['Library', 'Review', 'Taste', 'Activity', 'System']) {
			await expect(nav.getByRole('link', { name: new RegExp(`^${name}`) })).toBeVisible();
		}
	});
}
