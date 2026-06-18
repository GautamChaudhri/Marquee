import { env } from '$env/dynamic/public';
import { apiGet, type Fetch } from './client';
import { mockMetrics } from './mock';
import type { SystemMetrics } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getMetrics(fetch: Fetch): Promise<SystemMetrics> {
	if (useMocks()) return Promise.resolve(mockMetrics());
	return apiGet<SystemMetrics>(fetch, '/system/metrics');
}

export function getStatus(fetch: Fetch): Promise<Record<string, unknown>> {
	return apiGet<Record<string, unknown>>(fetch, '/system/status');
}
