import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { poll } from './poll';

beforeEach(() => {
	vi.useFakeTimers();
});
afterEach(() => {
	vi.useRealTimers();
});

describe('poll', () => {
	it('runs now, then again after each run ends, until stopped', async () => {
		const task = vi.fn();
		const stop = poll(task, 1000);
		await vi.advanceTimersByTimeAsync(0);
		expect(task).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(2500);
		expect(task).toHaveBeenCalledTimes(3);
		stop();
		await vi.advanceTimersByTimeAsync(5000);
		expect(task).toHaveBeenCalledTimes(3);
	});

	it('never overlaps a slow run and keeps going after a failure', async () => {
		let running = 0;
		let most = 0;
		const task = vi.fn(async () => {
			running += 1;
			most = Math.max(most, running);
			await new Promise((resolve) => setTimeout(resolve, 3000));
			running -= 1;
			throw new Error('a failed run');
		});
		const stop = poll(task, 1000);
		await vi.advanceTimersByTimeAsync(9000);
		stop();
		expect(most).toBe(1);
		expect(task.mock.calls.length).toBeGreaterThanOrEqual(2);
	});
});
