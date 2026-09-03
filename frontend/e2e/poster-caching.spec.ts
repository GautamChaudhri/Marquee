import { expect, test } from '@playwright/test';

/** The poster-loading contract: grids request versioned thumbnail URLs and
 *  load lazily; responses carry the caching policy through the node proxy;
 *  and a revisit never re-downloads an unchanged poster. */

test('library posters load lazily from versioned thumbnail URLs with cache headers', async ({
	page
}) => {
	const posterResponses: { url: string; status: number; cacheControl: string | null }[] = [];
	page.on('response', (response) => {
		if (response.url().includes('/poster')) {
			posterResponses.push({
				url: response.url(),
				status: response.status(),
				cacheControl: response.headers()['cache-control'] ?? null
			});
		}
	});

	await page.goto('/television');
	await expect(page.getByRole('heading', { name: 'Television Library' })).toBeVisible();

	const poster = page.getByRole('img', { name: 'Complete Edition show poster' });
	await expect(poster).toBeVisible();
	// Versioned for immutability, w=400 for the grid-weight derivative, lazy so
	// offscreen rows do not compete for the proxy's connections.
	await expect(poster).toHaveAttribute('src', /\/poster\?v=synthetic0001&w=400$/);
	await expect(poster).toHaveAttribute('loading', 'lazy');
	await expect(poster).toHaveAttribute('decoding', 'async');

	await expect
		.poll(() => posterResponses.length, { message: 'poster requests should be captured' })
		.toBeGreaterThan(0);
	const versioned = posterResponses.filter((entry) => entry.url.includes('v=synthetic0001'));
	expect(versioned.length).toBeGreaterThan(0);
	for (const entry of versioned) {
		expect(entry.status).toBe(200);
		expect(entry.cacheControl).toBe('public, max-age=31536000, immutable');
	}
});

test('a revisit re-downloads no unchanged posters', async ({ page, request }) => {
	const API = `http://127.0.0.1:${process.env.MARQUEE_E2E_API_PORT ?? 3199}`;
	const bodyServes = async () =>
		(await (await request.get(`${API}/__test/poster-body-serves`)).json()).count as number;

	// Warm both libraries so every poster URL is in the browser cache.
	await page.goto('/television');
	await expect(page.getByRole('img', { name: 'Complete Edition show poster' })).toBeVisible();
	await page.goto('/films');
	await expect(page.getByRole('img', { name: 'Midnight Archive poster' })).toBeVisible();
	const warmed = await bodyServes();
	expect(warmed).toBeGreaterThan(0);

	// The revisit: a poster served from the browser cache never reaches the
	// backend, and a revalidation 304 carries no body — so the full-body
	// counter must not move.
	await page.goto('/television');
	await expect(page.getByRole('img', { name: 'Complete Edition show poster' })).toBeVisible();
	await page.goto('/films');
	await expect(page.getByRole('img', { name: 'Midnight Archive poster' })).toBeVisible();

	expect(await bodyServes()).toBe(warmed);
});
