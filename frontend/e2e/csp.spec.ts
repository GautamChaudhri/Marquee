import { expect, test } from './fixtures';
import { resetBackend, signIn } from './helpers';

const POLICY = [
	"default-src 'self'",
	/script-src 'self' 'nonce-[A-Za-z0-9+/=_-]+'/,
	"style-src 'self'",
	// SvelteKit's route announcer carries an inline style attribute; nothing else inline.
	"style-src-attr 'unsafe-inline'",
	"img-src 'self' data: blob:",
	"font-src 'self'",
	"connect-src 'self'",
	"object-src 'none'",
	"base-uri 'self'",
	"form-action 'self'",
	"frame-ancestors 'none'"
];

test('every page carries the policy, with a nonce for its own scripts', async ({ page }) => {
	await resetBackend();
	const signin = await page.request.get('/signin');
	for (const response of [signin]) {
		const policy = response.headers()['content-security-policy'] ?? '';
		for (const part of POLICY) {
			if (typeof part === 'string') expect(policy).toContain(part);
			else expect(policy).toMatch(part);
		}
		// No 'unsafe-inline' anywhere but on style attributes, and no 'unsafe-eval'.
		expect(policy.replace("style-src-attr 'unsafe-inline'", '')).not.toContain('unsafe-inline');
		expect(policy).not.toContain('unsafe-eval');
		expect(response.headers()['x-frame-options']).toBe('DENY');
		// Served over plain HTTP: no HSTS.
		expect(response.headers()['strict-transport-security']).toBeUndefined();
	}
	// The nonce in the header is the one on the page's inline scripts, and changes each time.
	const html = await signin.text();
	const nonce = /'nonce-([^']+)'/.exec(signin.headers()['content-security-policy'] ?? '')![1];
	expect(html).toContain(`nonce="${nonce}"`);
	const again = await page.request.get('/signin');
	expect(again.headers()['content-security-policy']).not.toContain(nonce);

	await signIn(page);
	const library = await page.request.get('/');
	expect(library.headers()['content-security-policy']).toMatch(/script-src 'self' 'nonce-/);
});

test('answers that are not pages keep the three headers and need no policy', async ({ page }) => {
	await resetBackend();
	await signIn(page);
	const api = await page.request.get('/api/movies?page_size=1');
	expect(api.status()).toBe(200);
	expect(api.headers()['content-security-policy']).toBeUndefined();
	expect(api.headers()['x-content-type-options']).toBe('nosniff');
	expect(api.headers()['x-frame-options']).toBe('DENY');
	expect(api.headers()['referrer-policy']).toBe('same-origin');
});

test('the helper sees a violation when a page breaks the policy', async ({
	page,
	cspViolations
}) => {
	await resetBackend();
	await page.goto('/signin');
	await page.evaluate(() => {
		const script = document.createElement('script');
		script.textContent = 'window.__ran = true';
		document.head.append(script);
	});
	await expect.poll(() => cspViolations.length).toBeGreaterThan(0);
	expect(await page.evaluate(() => (window as unknown as { __ran?: boolean }).__ran)).toBe(
		undefined
	);
	// This test broke the policy on purpose; what it found is not a failure.
	cspViolations.length = 0;
});
