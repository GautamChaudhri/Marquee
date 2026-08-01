import { describe, expect, it } from 'vitest';
import { batchOptionsFor, clampChunkSize, DEFAULT_CHUNK_SIZE } from './batch-options';

describe('clampChunkSize', () => {
	it('holds the value inside the range the control offers', () => {
		expect(clampChunkSize(-4)).toBe(1);
		expect(clampChunkSize(99)).toBe(16);
		expect(clampChunkSize(8.6)).toBe(9);
	});

	it('falls back to the default for anything a cleared number input can produce', () => {
		expect(clampChunkSize(null)).toBe(DEFAULT_CHUNK_SIZE);
		expect(clampChunkSize(undefined)).toBe(DEFAULT_CHUNK_SIZE);
		expect(clampChunkSize('')).toBe(DEFAULT_CHUNK_SIZE);
		expect(clampChunkSize(Number.NaN)).toBe(DEFAULT_CHUNK_SIZE);
		// Zero is falsy, so it lands on the default rather than the floor — a batch of
		// none is not what anyone meant by clearing the field.
		expect(clampChunkSize(0)).toBe(DEFAULT_CHUNK_SIZE);
	});
});

describe('batchOptionsFor', () => {
	it('sends a clamped chunk size for chunked runs', () => {
		expect(batchOptionsFor('chunked', 12)).toEqual({ batch_mode: 'chunked', chunk_size: 12 });
		expect(batchOptionsFor('chunked', 99)).toEqual({ batch_mode: 'chunked', chunk_size: 16 });
	});

	it('omits chunk_size entirely for a unified run', () => {
		const options = batchOptionsFor('all_at_once', 12);
		expect(options).toEqual({ batch_mode: 'all_at_once' });
		expect(options).not.toHaveProperty('chunk_size');
	});
});
