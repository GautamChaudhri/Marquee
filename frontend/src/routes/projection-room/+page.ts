import type { PageLoad } from './$types';
import { getActivityAttention } from '$lib/activity/client';
import type { ActivityAttentionResponse } from '$lib/activity/types';

export const load: PageLoad = async ({ fetch }) => {
	const attention = await getActivityAttention(fetch).catch(
		(): ActivityAttentionResponse | null => null
	);
	return { attention };
};
