import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { installTasteFixtures } from './support/taste-fixtures';

test('Key Art Engine presents real profile subjects and isolates Film and TV data', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-08-02T12:00:00Z'));
	await installTasteFixtures(page);

	await page.goto('/taste?library=movies');
	await expect(page.getByRole('heading', { name: 'Key Art Engine' })).toBeAttached();
	await expect(page.getByText('Movies profile · generation 3').first()).toBeVisible();
	await expect(page.getByRole('button', { name: /Heat \(1995\).*2 posters/ })).toBeVisible();
	await page.getByRole('button', { name: /Heat \(1995\).*2 posters/ }).click();
	await expect(page.locator('.asset-row').filter({ hasText: 'Heat (1995)' })).toHaveCount(2);
	await expect(page.getByText('dup ×2').first()).toBeVisible();
	await expect(page.getByText('Dark Matter')).toHaveCount(0);

	await page
		.getByRole('navigation', { name: 'Taste library' })
		.getByRole('link', { name: 'Television' })
		.click();
	await expect(page).toHaveURL(/\/taste\?library=tv$/);
	await expect(page.getByText('TV profile · generation 2').first()).toBeVisible();
	await expect(
		page.getByRole('button', { name: /Dark Matter \(2024\).*1 show · 2 seasons/ })
	).toBeVisible();
	await page.getByRole('button', { name: /Dark Matter \(2024\).*1 show · 2 seasons/ }).click();
	await expect(page.getByText('Dark Matter (2024) · Season 1')).toBeVisible();
	await expect(page.getByText('Heat (1995)', { exact: true })).toHaveCount(0);

	await page.getByRole('button', { name: 'Hide map' }).click();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test('Key Art Engine mutations preserve library and source and surface inline activity', async ({
	page
}) => {
	const fixtures = await installTasteFixtures(page);
	await page.goto('/taste?library=movies');
	await page.getByRole('button', { name: 'Build from seeding bundle' }).click();
	const seeded = await fixtures.expectLastRequest('/api/taste/retrain', 'movies');
	expect(seeded.body).toEqual({ library: 'movies', source: 'seeding_bundle' });
	await expect(page.getByRole('heading', { name: 'Taste training activity' })).toBeVisible();
	await expect(page.locator('article.activity-row')).toContainText('Rebuild taste profile');

	await page
		.getByRole('navigation', { name: 'Taste library' })
		.getByRole('link', { name: 'Television' })
		.click();
	await page.getByRole('button', { name: 'Rebuild profile' }).click();
	const rebuilt = await fixtures.expectLastRequest('/api/taste/retrain', 'tv');
	expect(rebuilt.body).toEqual({ library: 'tv' });

	await page.getByRole('button', { name: 'Hide map' }).click();
	await page.getByRole('button', { name: 'Enrich metadata' }).click();
	await fixtures.expectLastRequest('/api/taste/enrich', 'tv');
	await expect(page.getByRole('heading', { name: 'Taste training activity' })).toBeVisible();
	await expect(page.locator('article.activity-row')).toContainText('Enrich taste metadata');
});

test('Key Art Engine remains accessible at a mobile viewport', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await installTasteFixtures(page);
	await page.goto('/taste?library=tv');
	await page.getByRole('button', { name: 'Hide map' }).click();
	await expect(page.getByText('TV profile · generation 2').first()).toBeVisible();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
