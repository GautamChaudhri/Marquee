import { apiGet, apiSend, type Fetch } from './client';

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
	values: Record<string, unknown>;
	defaults: Record<string, unknown>;
	overrides: Record<string, unknown>;
	restart_required: string[];
	groups: KnobGroup[];
	meta: Record<string, KnobMeta>;
}

export interface ConfigUpdateResult {
	applied: string[];
	overrides: Record<string, unknown>;
}

export interface PosterResetResult {
	reset: number;
	runs_cleared: number;
	posters_reset: number;
}

export function getPipelineConfig(fetchFn: Fetch): Promise<PipelineConfig> {
	return apiGet<PipelineConfig>(fetchFn, '/config/pipeline');
}

export function putPipelineConfig(
	fetchFn: Fetch,
	values: Record<string, unknown>
): Promise<ConfigUpdateResult> {
	return apiSend<ConfigUpdateResult>(fetchFn, 'PUT', '/config/pipeline', { values });
}

export function resetDeployedPosters(fetchFn: Fetch): Promise<PosterResetResult> {
	return apiSend<PosterResetResult>(fetchFn, 'POST', '/pipeline/posters/reset');
}
