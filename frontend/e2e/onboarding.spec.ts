import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test.describe('recoverable taste onboarding', () => {
	test('renders neutral review evidence and records a keyboard choice as intent only', async ({
		page
	}, testInfo) => {
		let chooseBody: unknown = null;
		page.on('request', (request) => {
			if (new URL(request.url()).pathname === '/api/onboarding/choose') {
				chooseBody = request.postDataJSON();
			}
		});

		await page.goto('/onboarding');
		const review = page.getByTestId('candidate-review');
		await expect(review).toBeVisible();
		await expect(review).toContainText('Synthetic First Movie');
		await expect(review).not.toContainText(/\b(rank|score|recommendation)\b/i);
		await expect(
			review.getByRole('img', { name: 'Poster choice from Synthetic archive' })
		).toBeVisible();

		const choose = review.getByRole('button', { name: 'Choose this poster' });
		await choose.focus();
		await choose.press('Enter');
		await expect(review).toContainText('Your choice was recorded');
		await expect(choose).toBeDisabled();
		expect(chooseBody).toEqual({
			run_id: 'onboardingreview000000000000001',
			candidate_id: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
			review_revision: 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
			idempotency_key: expect.stringMatching(/^onboarding:choose:/)
		});

		const results = await new AxeBuilder({ page })
			.include('[data-testid="candidate-review"]')
			.analyze();
		expect(results.violations).toEqual([]);
		await testInfo.attach('onboarding-axe-violations.json', {
			body: JSON.stringify(results.violations, null, 2),
			contentType: 'application/json'
		});
	});

	test('records dislike without creating a deployment request', async ({ page }) => {
		let hateBody: unknown = null;
		page.on('request', (request) => {
			if (new URL(request.url()).pathname === '/api/onboarding/hate') {
				hateBody = request.postDataJSON();
			}
		});

		await page.goto('/onboarding');
		const review = page.getByTestId('candidate-review');
		await review.getByRole('button', { name: 'I do not want this' }).click();
		await expect(review).toContainText('No poster deployment was created');
		expect(hateBody).toEqual({
			run_id: 'onboardingreview000000000000001',
			candidate_id: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
			review_revision: 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
			idempotency_key: expect.stringMatching(/^onboarding:hate:/)
		});
	});

	test('keeps review controls usable on a narrow viewport', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto('/onboarding');
		const review = page.getByTestId('candidate-review');
		await expect(review).toBeVisible();
		await expect(review.getByRole('button', { name: 'Choose this poster' })).toBeVisible();
		await expect(review.getByRole('button', { name: 'I do not want this' })).toBeVisible();
	});
});
