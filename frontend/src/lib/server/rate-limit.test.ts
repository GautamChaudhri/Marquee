import { describe, expect, it } from 'vitest';
import { FailureLimiter, MAX_FAILURES, WINDOW_MS } from './rate-limit';

describe('failed sign-ins', () => {
	it('lets four failures through and makes the fifth wait out the minute', () => {
		const limiter = new FailureLimiter();
		const start = 1_000_000;
		for (let n = 0; n < MAX_FAILURES; n += 1) {
			expect(limiter.retryAfterMs('a', start + n * 1000)).toBe(0);
			limiter.fail('a', start + n * 1000);
		}
		// Five failures within a minute: wait until the first of them is a minute old.
		expect(limiter.retryAfterMs('a', start + 5000)).toBe(WINDOW_MS - 5000);
		expect(limiter.retryAfterMs('a', start + WINDOW_MS - 1)).toBe(1);
		expect(limiter.retryAfterMs('a', start + WINDOW_MS)).toBe(0);
	});

	it('counts each address on its own', () => {
		const limiter = new FailureLimiter();
		for (let n = 0; n < MAX_FAILURES; n += 1) limiter.fail('a', n);
		expect(limiter.retryAfterMs('a', MAX_FAILURES)).toBeGreaterThan(0);
		expect(limiter.retryAfterMs('b', MAX_FAILURES)).toBe(0);
	});

	it('forgets failures older than the window', () => {
		const limiter = new FailureLimiter();
		for (let n = 0; n < MAX_FAILURES - 1; n += 1) limiter.fail('a', n);
		limiter.fail('a', WINDOW_MS + 10);
		expect(limiter.retryAfterMs('a', WINDOW_MS + 10)).toBe(0);
	});
});
