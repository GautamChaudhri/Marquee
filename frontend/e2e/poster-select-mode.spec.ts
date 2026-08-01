import { expect, test, type Page } from '@playwright/test';

const selectButton = (page: Page) => page.getByRole('button', { name: 'Select', exact: true });
const doneButton = (page: Page) => page.getByRole('button', { name: 'Done', exact: true });
const runSelected = (page: Page) => page.getByRole('button', { name: /^Run selected/ });

test('the Films Run tab hides its checkboxes until you ask to select', async ({ page }) => {
	await page.goto('/pipeline/movies');

	// Nothing is armed until selection is explicit.
	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await expect(runSelected(page)).toHaveCount(0);

	await selectButton(page).click();
	await expect(page.getByRole('checkbox', { name: 'Select Synthetic Feature One' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'All', exact: true })).toBeVisible();
	await expect(runSelected(page)).toHaveCount(0);

	await page.getByRole('checkbox', { name: 'Select Synthetic Feature One' }).check();
	await expect(runSelected(page)).toHaveText(/Run selected \(1\)/);

	// The per-card Run action is not smothered by select mode.
	await expect(
		page.getByRole('button', { name: 'Run poster pipeline for Synthetic Feature One' })
	).toHaveCount(1);

	// Leaving select mode must drop the selection with the checkboxes, or a bulk
	// command stays armed with nothing on screen to show for it.
	await doneButton(page).click();
	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await expect(runSelected(page)).toHaveCount(0);
});

test('the Films select-all pill selects and clears every eligible card', async ({ page }) => {
	await page.goto('/pipeline/movies');

	await selectButton(page).click();
	await page.getByRole('button', { name: 'All', exact: true }).click();
	await expect(runSelected(page)).toHaveText(/Run selected \(2\)/);

	await page.getByRole('button', { name: 'None', exact: true }).click();
	await expect(runSelected(page)).toHaveCount(0);
});

test('the Television Run tab gates its checkboxes the same way', async ({ page }) => {
	await page.goto('/pipeline/tv');

	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await expect(runSelected(page)).toHaveCount(0);

	await selectButton(page).click();
	await expect(page.getByRole('checkbox', { name: 'Select Synthetic Series One' })).toBeVisible();

	await page.getByRole('checkbox', { name: 'Select Synthetic Series One' }).check();
	await expect(runSelected(page)).toHaveText(/Run selected \(1\)/);

	// The per-row Run pill stays: TV rows have the width for it.
	await expect(page.getByRole('button', { name: 'Run', exact: true })).toHaveCount(2);

	await doneButton(page).click();
	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await expect(runSelected(page)).toHaveCount(0);
});
