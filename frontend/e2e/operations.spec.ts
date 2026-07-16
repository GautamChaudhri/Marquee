import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('loads bounded Operations diagnostics only when requested and stops when hidden', async ({
	page
}) => {
	const operationsRequests: string[] = [];
	const historyRequests: string[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.pathname === '/api/system/operations') operationsRequests.push(url.href);
		if (url.pathname === '/api/system/metrics/history') historyRequests.push(url.href);
	});

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/projection-room');
	await expect(page.getByRole('heading', { name: 'Queue' })).toBeVisible();
	await expect.poll(() => operationsRequests.length).toBe(0);
	await expect.poll(() => historyRequests.length).toBe(0);

	await page.getByRole('button', { name: /Operations/ }).click();
	await expect(page.getByRole('heading', { name: 'Operations', exact: true })).toBeVisible();
	await expect(page.getByText('Synthetic CPU')).toBeVisible();
	await expect.poll(() => operationsRequests.length).toBe(1);
	await expect.poll(() => historyRequests.length).toBe(0);

	await page.getByLabel('History window').selectOption('15m');
	await page.getByRole('button', { name: 'Load bounded history' }).click();
	await expect(page.getByLabel('Operations history')).toContainText('1 downsampled points');
	await expect.poll(() => historyRequests.length).toBe(1);
	const historyUrl = new URL(historyRequests[0]);
	expect(historyUrl.searchParams.get('window')).toBe('15m');
	expect(historyUrl.searchParams.get('resolution')).toBe('120');

	const results = await new AxeBuilder({ page }).analyze();
	expect(results.violations).toEqual([]);

	await page.getByRole('button', { name: /Queue/ }).click();
	await expect(page.getByRole('heading', { name: 'Queue' })).toBeVisible();
	await page.waitForTimeout(400);
	expect(operationsRequests).toHaveLength(1);
	expect(historyRequests).toHaveLength(1);
});
