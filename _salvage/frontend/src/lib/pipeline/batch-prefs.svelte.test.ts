import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

async function loadPrefs() {
	vi.resetModules();
	return import('./batch-prefs');
}

describe('poster batch preferences', () => {
	beforeEach(() => {
		localStorage.clear();
	});

	it('starts at chunks of eight with nothing stored', async () => {
		const { posterBatchMode, posterBatchChunkSize } = await loadPrefs();

		expect(get(posterBatchMode)).toBe('chunked');
		expect(get(posterBatchChunkSize)).toBe(8);
	});

	it('restores what was stored', async () => {
		localStorage.setItem('marquee:posterBatchMode', JSON.stringify('all_at_once'));
		localStorage.setItem('marquee:posterBatchChunkSize', JSON.stringify(12));
		const { posterBatchMode, posterBatchChunkSize } = await loadPrefs();

		expect(get(posterBatchMode)).toBe('all_at_once');
		expect(get(posterBatchChunkSize)).toBe(12);
	});

	it('sanitises a value the number input could have persisted mid-edit', async () => {
		localStorage.setItem('marquee:posterBatchChunkSize', JSON.stringify(null));
		localStorage.setItem('marquee:posterBatchMode', JSON.stringify('nonsense'));
		const { posterBatchMode, posterBatchChunkSize } = await loadPrefs();

		expect(get(posterBatchChunkSize)).toBe(8);
		expect(get(posterBatchMode)).toBe('chunked');
	});

	it('writes changes back so the next visit reads them', async () => {
		const { posterBatchMode } = await loadPrefs();

		posterBatchMode.set('all_at_once');

		expect(localStorage.getItem('marquee:posterBatchMode')).toBe(JSON.stringify('all_at_once'));
	});
});
