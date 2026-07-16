import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const jobId = 'detail00000000000000000000000001';

test('loads canonical job diagnostics lazily and keeps retained evidence explicit', async ({
	page
}) => {
	const diagnostics: string[] = [];
	page.on('request', (request) => {
		if (request.url().includes(`/api/jobs/${jobId}/`)) diagnostics.push(request.url());
	});

	await page.goto(`/projection-room/jobs/${jobId}`);
	await expect(page.getByRole('heading', { name: 'Deleted synthetic movie' })).toBeVisible();
	await expect(page.getByText('live library subject was deleted')).toBeVisible();
	await expect(page.getByText('English', { exact: true })).toBeVisible();
	expect(diagnostics.some((url) => url.includes('/events'))).toBe(false);
	expect(diagnostics.some((url) => url.includes('/attempts'))).toBe(false);

	await page.getByRole('button', { name: 'Timeline' }).click();
	await expect(page.getByText('Encoder stopped')).toBeVisible();
	expect(diagnostics.some((url) => url.includes('/events'))).toBe(true);

	await page.getByRole('button', { name: 'Logs' }).click();
	await expect(page.getByText('Earlier output was truncated.')).toBeVisible();
	await expect(page.getByText('secret=[REDACTED] synthetic failure')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Download attempt log' })).toHaveAttribute(
		'href',
		`/api/jobs/${jobId}/attempts/2/logs/download`
	);

	await page.getByRole('button', { name: 'Artifacts' }).click();
	await expect(page.getByText('stderr.txt')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Download' })).toHaveAttribute(
		'href',
		`/api/jobs/${jobId}/artifacts/1/download`
	);

	await page.getByRole('button', { name: 'Raw Data' }).click();
	await expect(page.getByText(/\[REDACTED\]/)).toBeVisible();
	await expect(page.getByRole('link', { name: 'Download request' })).toHaveAttribute(
		'href',
		`/api/jobs/${jobId}/raw/request?download=true`
	);

	await page.getByRole('button', { name: 'Execution' }).click();
	await expect(page.getByText('Attempt #2')).toBeVisible();
});

test('keeps canonical job detail responsive and free of axe violations', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/projection-room/jobs/${jobId}`);
	await expect(page.getByRole('button', { name: 'Raw Data' })).toBeVisible();
	const results = await new AxeBuilder({ page }).analyze();
	expect(results.violations).toEqual([]);
});
