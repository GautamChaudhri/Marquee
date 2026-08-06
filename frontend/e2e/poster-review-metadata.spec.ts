import { expect, test } from '@playwright/test';

test('Television review previews keep each asset candidate state and age together', async ({
	page
}) => {
	await page.goto('/pipeline/tv?tab=review');

	const card = page.locator('.review-card').filter({ hasText: 'Synthetic Review Series' });
	await expect(card).toBeVisible();

	const show = card.locator('.preview-tile').filter({ hasText: 'Show' });
	await expect(show.locator('.preview-stats')).toContainText('5 candidates');
	await expect(show.locator('.preview-age')).toHaveText(/just now|\d+[mhd] ago|—/);
	await expect(show.locator('.preview-stats > span').first()).toHaveClass(/warn/);

	const season = card.locator('.preview-tile').filter({ hasText: 'S01' });
	await expect(season.locator('.preview-stats')).toContainText('0 candidates');
	await expect(season.locator('.preview-age')).toHaveText(/just now|\d+[mhd] ago|—/);
	await expect(season.locator('.preview-stats > span').first()).toHaveClass(/bad/);
	await expect(season.locator('.preview-placeholder .poster')).toHaveClass(/centered-title/);
	await expect(season.getByText('No pick')).toHaveCount(0);
	await expect(card.locator('.preview-tile .dot')).toHaveCount(0);
});
