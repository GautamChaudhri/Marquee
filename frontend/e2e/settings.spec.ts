import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const tabs = [
	'General',
	'Connections',
	'Media',
	'Posters',
	'Pipeline',
	'Taste',
	'System',
	'Access'
];

test('settings exposes eight URL-addressable keyboard tabs and only needed advanced views', async ({
	page
}) => {
	await page.goto('/settings');
	const tablist = page.getByRole('tablist', { name: 'Settings sections' });
	await expect(tablist.getByRole('tab')).toHaveCount(8);
	await expect(page.getByRole('heading', { name: 'General', exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Advanced' })).toHaveCount(0);

	for (const label of tabs) {
		await tablist.getByRole('tab', { name: label }).click();
		await expect(page).toHaveURL(new RegExp(`[?&]tab=${label.toLowerCase()}`));
	}

	await tablist.getByRole('tab', { name: 'Pipeline' }).click();
	await page.getByRole('button', { name: 'Advanced' }).click();
	await expect(page).toHaveURL(/tab=pipeline.*level=advanced/);
	await expect(page.getByText('Scoring Weights')).toBeVisible();

	await tablist.getByRole('tab', { name: 'Pipeline' }).focus();
	await page.keyboard.press('ArrowRight');
	await expect(tablist.getByRole('tab', { name: 'Taste' })).toBeFocused();
	await expect(page).toHaveURL(/[?&]tab=taste/);
});

test('settings keeps drafts visible, saves through the unified API, and stays accessible', async ({
	page
}) => {
	await page.goto('/settings?tab=general&level=advanced');
	await expect(page).toHaveURL(/tab=general(?!.*level=advanced)/);
	const appName = page.getByLabel('App Name');
	await appName.fill('Marquee Screening Room');
	await expect(page.getByText('1 unsaved change')).toBeVisible();
	await expect(
		page.getByRole('tab', { name: 'General' }).getByLabel('Unsaved changes')
	).toBeVisible();
	await page.getByRole('button', { name: 'Save all changes' }).click();
	await expect(appName).toHaveValue('Marquee Screening Room');
	await expect(page.getByText('1 unsaved change')).toHaveCount(0);

	const results = await new AxeBuilder({ page }).analyze();
	expect(results.violations).toEqual([]);
});

test('settings tab rail scrolls instead of clipping on a phone viewport', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/settings?tab=posters');
	await expect(page.getByRole('tab', { name: 'Posters' })).toBeVisible();
	const scrollable = await page
		.locator('.tab-scroll')
		.evaluate(
			(element) =>
				element.scrollWidth > element.clientWidth && element.getBoundingClientRect().width > 0
		);
	expect(scrollable).toBe(true);
	await expect(page.locator('body')).not.toHaveCSS('overflow-x', 'scroll');
});
