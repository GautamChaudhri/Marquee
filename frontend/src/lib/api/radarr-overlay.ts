import { env } from '$env/dynamic/public';
import { apiGet, type Fetch } from './client';
import { mockRadarrOverlay } from './mock';
import type { RadarrOverlayQuery, RadarrOverlayResponse } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getRadarrOverlay(
	fetch: Fetch,
	params: RadarrOverlayQuery = {}
): Promise<RadarrOverlayResponse> {
	if (useMocks()) return Promise.resolve(mockRadarrOverlay(params));
	const query = {
		...params,
		hdr_tags: params.hdr_tags?.join(',')
	};
	return apiGet<RadarrOverlayResponse>(
		fetch,
		'/hdr',
		query as unknown as Record<string, unknown>
	);
}
