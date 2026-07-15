import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import type { ConfigurationHealth } from './types';

type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

export interface KnobMeta {
	kind: 'weight' | 'float' | 'int' | 'bool' | 'enum' | 'str';
	min?: number;
	max?: number;
	step?: number;
	options?: string[];
	help?: string;
}

export interface KnobGroup {
	id: string;
	label: string;
	description: string;
	knobs: string[];
}

export interface PipelineConfig {
	configuration_version: number;
	etag: string;
	stale: boolean;
	health: ConfigurationHealth;
	values: Record<string, unknown>;
	defaults: Record<string, unknown>;
	overrides: Record<string, unknown>;
	restart_required: string[];
	groups: KnobGroup[];
	meta: Record<string, KnobMeta>;
}

export interface ConfigUpdateResult {
	configuration_version: number;
	etag: string;
	changed: boolean;
	applied: string[];
	overrides: Record<string, unknown>;
}

export function getPipelineConfig(fetchFn: Fetch): Promise<PipelineConfig> {
	return apiGet<PipelineConfig>(fetchFn, '/config/pipeline');
}

export function putPipelineConfig(
	fetchFn: Fetch,
	values: Record<string, unknown>,
	expectedVersion: number
): Promise<ConfigUpdateResult> {
	return apiSend<ConfigUpdateResult>(fetchFn, 'PUT', '/config/pipeline', {
		expected_version: expectedVersion,
		values
	});
}

export function resetDeployedPosters(fetchFn: Fetch): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		'/pipeline/posters/reset',
		{},
		{ 'Idempotency-Key': `poster_deploy_reset:${crypto.randomUUID()}` }
	);
}
