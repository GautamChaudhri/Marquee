import { expect, test } from '@playwright/test';
import { receivedBodies, resetBackend, signIn } from './helpers';

test.beforeEach(async ({ page }) => {
	await signIn(page);
});

test('with no evaluation yet the Taste page says so and offers to run one', async ({ page }) => {
	await resetBackend();
	await page.goto('/taste');
	const block = page.getByTestId('evaluation');
	await expect(block).toContainText('No blind evaluation yet.');
	await expect(block.getByRole('button', { name: 'Run a blind evaluation' })).toBeEnabled();
});

test('a finished evaluation reads as a sentence, beside the one before', async ({ page }) => {
	await resetBackend({ evaluations: 'finished' });
	await page.goto('/taste');
	await expect(page.getByTestId('evaluation-headline')).toHaveText(
		'On 3 posters you picked by hand, the blind pick was your design 0 times (0%); your ' +
			'design was in the first three 1 time; 1 of your posters was removed by a gate; 1 is ' +
			'not among TMDB’s candidates.'
	);
	await expect(page.getByTestId('evaluation')).toContainText(
		'Posters you chose from Marquee’s picks are counted apart: 1 of 1 were the blind pick’s design.'
	);
	await expect(page.getByTestId('evaluation-previous')).toHaveText(
		'Before: Evaluation 1: your design was the pick 0 of 2 (0%), in the first three 1.'
	);
	await page.getByRole('link', { name: 'See every poster' }).click();
	await expect(page).toHaveURL('/taste/evaluations/2');
});

test('the results page lists every poster, filters, and opens a masked run', async ({ page }) => {
	await resetBackend({ evaluations: 'finished' });
	await page.goto('/taste/evaluations/2');
	await expect(page.getByTestId('evaluation-summary')).toContainText('Blind evaluation 2');
	const rows = page.getByTestId('evaluation-result');
	await expect(rows).toHaveCount(4);
	await expect(rows.nth(0)).toContainText('Arrival');
	await expect(rows.nth(0)).toContainText('Removed by the text gate: text_heavy.');
	await expect(rows.nth(1)).toContainText('Your poster was the blind pick.');
	await expect(rows.nth(1)).toContainText('Chosen in a review');
	await expect(rows.nth(2)).toContainText('Your design was 2nd of 2.');
	await expect(rows.nth(2)).toContainText('found by likeness (0.97)');
	await expect(rows.nth(3)).toContainText(
		'Not among TMDB’s candidates. The closest candidate’s likeness was 0.81.'
	);
	await expect(rows.nth(2).getByAltText('The blind pick for Heat')).toBeVisible();
	await expect(rows.nth(3)).toContainText('Its run was replaced by a newer evaluation.');

	await page.getByLabel('What happened').selectOption('ranked');
	await expect(rows).toHaveCount(2);
	await page.getByLabel('Your poster').selectOption('seed');
	await expect(rows).toHaveCount(1);
	await page.getByLabel('What happened').selectOption('failed');
	await expect(page.getByText('No movie matches.')).toBeVisible();
	await page.getByLabel('What happened').selectOption('');

	await page.getByLabel('Your poster').selectOption('');
	await expect(rows).toHaveCount(4);
	await rows.filter({ hasText: 'Heat' }).getByRole('link', { name: 'Open the masked run' }).click();
	await expect(page).toHaveURL('/review/20');
	await expect(
		page.getByText('This is a masked run of a blind evaluation', { exact: false })
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Keep the pick' })).toHaveCount(0);
	await expect(page.getByTestId('answers')).toHaveCount(0);
});

test('running an evaluation asks first, then follows it to its end', async ({ page }) => {
	await resetBackend();
	await page.goto('/taste');
	await page.getByRole('button', { name: 'Run a blind evaluation' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('Run a blind evaluation?');
	await expect(dialog).toContainText('It changes no poster in your library');
	await expect(dialog).toContainText('on the GPU');
	await dialog.getByRole('button', { name: 'Run it' }).click();
	await expect(page.getByTestId('evaluation-progress')).toContainText('Evaluation 1 is running');
	await expect(page.getByRole('button', { name: 'Run a blind evaluation' })).toBeDisabled();
	await expect(page.getByTestId('evaluation-headline')).toContainText('On 3 posters', {
		timeout: 10_000
	});
	await expect(page.getByTestId('evaluation-progress')).toHaveCount(0);
	expect(await receivedBodies('/api/taste/evaluations')).toEqual([{}]);
});

test('an evaluation in progress shows its progress and its results so far', async ({ page }) => {
	await resetBackend({ evaluations: 'running' });
	await page.goto('/taste');
	await expect(page.getByTestId('evaluation-progress')).toContainText(
		'Evaluation 3 is running: 1 of 4 movies done, 1 running; 1 with a result so far.'
	);
	await expect(page.getByRole('button', { name: 'Run a blind evaluation' })).toBeDisabled();
	await expect(page.getByTestId('evaluation-headline')).toContainText('On 3 posters');
	await page.getByRole('link', { name: 'Results so far' }).click();
	await expect(page.getByTestId('evaluation-summary')).toContainText('Still running');
	await expect(page.getByTestId('evaluation-result')).toHaveCount(1);
});

test('an active residual says what it leans on and how much better it does', async ({ page }) => {
	await resetBackend({ residual: 'active' });
	await page.goto('/taste');
	await expect(page.getByTestId('residual-state')).toHaveText('Runs rank with the residual.');
	const residual = page.getByTestId('residual');
	await expect(residual).toContainText(
		'On 6 held-out movies, it agrees with your order 81% of the time, against 62% without it.'
	);
	await expect(residual).toContainText('Last training 4: activated, from 30 movies and 840 pairs.');
	await expect(residual.locator('li').first()).toContainText('title_colorfulness');
	await expect(residual.locator('li').first()).toContainText('more is better (2.40)');
	await expect(residual).not.toContainText('It needs feedback');
});

for (const width of [390, 1440]) {
	test(`the results page fits at ${width} px`, async ({ page }) => {
		await resetBackend({ evaluations: 'finished' });
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/taste/evaluations/2');
		await expect(page.getByTestId('evaluation-result')).toHaveCount(4);
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth - document.documentElement.clientWidth
		);
		expect(overflow).toBeLessThanOrEqual(0);
	});
}
