/** The one place the poster batch knobs are normalised into a request body.
 *  Kept free of `$app` imports so it stays a plain, testable module. */
import type { PosterBatchMode, PosterBatchOptions } from '$lib/api/types';

export const DEFAULT_CHUNK_SIZE = 8;
export const MIN_CHUNK_SIZE = 1;
export const MAX_CHUNK_SIZE = 16;

export function clampChunkSize(value: unknown): number {
	return Math.min(
		MAX_CHUNK_SIZE,
		Math.max(MIN_CHUNK_SIZE, Math.round(Number(value) || DEFAULT_CHUNK_SIZE))
	);
}

/** A unified run has no chunk size at all — the key is omitted, not zeroed, so the
 *  backend never sees a size that does not apply to the mode it was sent with. */
export function batchOptionsFor(mode: PosterBatchMode, chunkSize: number): PosterBatchOptions {
	if (mode === 'all_at_once') return { batch_mode: mode };
	return { batch_mode: mode, chunk_size: clampChunkSize(chunkSize) };
}
