import { apiSend, type Fetch } from './client';
import type { PipelineRunRef } from './types';

export function triggerRun(fetchFn: Fetch, movieId: number): Promise<PipelineRunRef> {
	return apiSend<PipelineRunRef>(fetchFn, 'POST', `/pipeline/movie/${movieId}/run`);
}
