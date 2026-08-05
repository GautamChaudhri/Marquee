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
	// General has no advanced entries, so no disclosure is offered.
	await expect(page.getByRole('button', { name: /advanced/i })).toHaveCount(0);

	for (const label of tabs) {
		await tablist.getByRole('tab', { name: label }).click();
		await expect(page).toHaveURL(new RegExp(`[?&]tab=${label.toLowerCase()}`));
	}

	// Advanced expands in place below the standard cards rather than replacing
	// them, so both sets stay on screen and the deep link still round-trips.
	await tablist.getByRole('tab', { name: 'Pipeline' }).click();
	await expect(page.getByRole('heading', { name: 'Runtime Defaults' })).toBeVisible();
	await page.getByRole('button', { name: 'Show advanced' }).click();
	await expect(page).toHaveURL(/tab=pipeline.*level=advanced/);
	await expect(page.getByRole('heading', { name: 'Scoring Weights' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Runtime Defaults' })).toBeVisible();

	await page.getByRole('button', { name: 'Hide advanced' }).click();
	await expect(page).toHaveURL(/tab=pipeline(?!.*level=advanced)/);
	await expect(page.getByRole('heading', { name: 'Scoring Weights' })).toHaveCount(0);

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

	// Resetting drops the override rather than writing the default value. The row's
	// Reset button is the proof: it only renders while a stored override exists, so
	// writing the default back would leave it on screen instead of clearing it.
	const row = page.locator('.setting-row', { has: page.getByLabel('App Name') });
	await expect(row.getByRole('button', { name: 'Reset' })).toBeVisible();
	await row.getByRole('button', { name: 'Reset' }).click();
	await page.getByRole('button', { name: 'Save all changes' }).click();
	await expect(row.getByRole('button', { name: 'Reset' })).toHaveCount(0);

	const results = await new AxeBuilder({ page }).analyze();
	expect(results.violations).toEqual([]);
});

test('media paths group Films and Television, support extra mappings, and keep app checks advanced', async ({
	page
}) => {
	await page.goto('/settings?tab=media');
	await expect(page.getByRole('heading', { name: 'Library paths' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Films' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Television' })).toBeVisible();
	await expect(page.getByText('Remote prefix')).toHaveCount(0);

	await page.getByRole('button', { name: /Add film path/ }).click();
	await expect(page.getByLabel('Radarr Path 2')).toBeVisible();
	await expect(page.getByLabel('Marquee Path Films 2')).toBeVisible();

	await page.getByRole('button', { name: 'Test accessibility' }).click();
	await expect(page.getByRole('region', { name: 'Marquee path checks' })).toContainText('readable');

	await page.getByRole('button', { name: /Show advanced/ }).click();
	const applicationPaths = page.getByRole('heading', { name: 'Application paths' });
	await expect(applicationPaths).toBeVisible();
	const dataPath = page.locator('.setting-row', { has: page.getByLabel('Data Dir') });
	await expect(dataPath).toContainText('Readable');
	await expect(dataPath).toContainText('Writable');
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

test('connections split managed instances from built-in metadata providers', async ({ page }) => {
	await page.goto('/settings?tab=connections');
	const registry = page.getByRole('region', { name: 'Connected Services' });
	const table = registry.getByRole('table');
	await expect(table.getByRole('columnheader', { name: 'Connection' })).toBeVisible();

	// TMDB is built in, so it belongs to the providers table and must not appear
	// among the instances the user can configure.
	await expect(table.locator('.built-in-mark')).toHaveCount(0);
	await expect(table.locator('tbody tr')).toHaveCount(2);

	const radarrRow = table.locator('tbody tr', { hasText: 'Cinema Rack' });
	await radarrRow.getByRole('button', { name: 'Edit', exact: true }).click();
	const dialog = page.getByRole('dialog', { name: 'Edit Radarr' });
	await expect(dialog).toBeVisible();
	await dialog.getByLabel('Instance name').fill('Screening Room Movies');
	await dialog.getByRole('button', { name: 'Test & save' }).click();
	await expect(dialog).toHaveCount(0);
	const updatedRadarrRow = table.locator('tbody tr', { hasText: 'Screening Room Movies' });
	await expect(updatedRadarrRow).toContainText('Connected');

	const providers = page.getByRole('region', { name: 'Metadata Providers' });
	await expect(providers.locator('.built-in-mark')).toHaveText('Marquee built-in');
	// Built-in providers are fixed, so neither table offers a way to add one.
	await expect(page.getByRole('button', { name: /add connection/i })).toHaveCount(0);

	await providers.getByRole('button', { name: 'Edit credential' }).click();
	await expect(page.getByRole('dialog', { name: 'Edit TMDB' })).toContainText('Marquee built-in');
});
