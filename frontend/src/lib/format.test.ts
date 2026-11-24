import { describe, expect, it } from 'vitest';
import { formatBytes, formatDuration, formatScore, formatWhen } from './format';

describe('formatting', () => {
	it('dates, and nothing for none', () => {
		expect(formatWhen('2026-11-24T12:00:00Z', 'en-GB')).toMatch(/24 Nov 2026/);
		expect(formatWhen(null)).toBe('—');
		expect(formatWhen('not a date')).toBe('—');
	});

	it('durations', () => {
		expect(formatDuration('2026-11-24T12:00:00Z', '2026-11-24T12:00:21Z')).toBe('21 s');
		expect(formatDuration('2026-11-24T12:00:00Z', '2026-11-24T12:02:05Z')).toBe('2 min 5 s');
		expect(formatDuration('2026-11-24T12:00:00Z', null)).toBe('—');
	});

	it('sizes and scores', () => {
		expect(formatBytes(512)).toBe('512 B');
		expect(formatBytes(158_445)).toBe('154.7 KB');
		expect(formatBytes(62_000_000_000)).toBe('57.7 GB');
		expect(formatBytes(null)).toBe('—');
		expect(formatScore(0.884351)).toBe('0.884');
		expect(formatScore(null)).toBe('—');
	});
});
