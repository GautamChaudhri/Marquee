/** Batching is a standing preference, not a per-visit choice: both poster workspaces
 *  read these, so picking Unified on Films keeps Unified on Television, across reloads. */
import { persisted } from '$lib/persisted';
import type { PosterBatchMode } from '$lib/api/types';
import { clampChunkSize, DEFAULT_CHUNK_SIZE } from './batch-options';

export const posterBatchMode = persisted<PosterBatchMode>('marquee:posterBatchMode', 'chunked');
export const posterBatchChunkSize = persisted<number>(
	'marquee:posterBatchChunkSize',
	DEFAULT_CHUNK_SIZE
);

// Hydration guard, not decoration. `bind:value` on a number input writes null while the
// field is empty, and that null is persisted before blur normalises it — so whatever comes
// back out of localStorage is sanitised once, here, rather than at every read site.
posterBatchMode.update((mode) => (mode === 'all_at_once' ? 'all_at_once' : 'chunked'));
posterBatchChunkSize.update(clampChunkSize);
