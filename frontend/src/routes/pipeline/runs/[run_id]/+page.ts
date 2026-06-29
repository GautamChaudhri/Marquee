import type { PageLoad } from './$types';
import { getOcrLabelState, getRunResults } from '$lib/api/pipeline';
import { getSettings } from '$lib/api/system';
import type { OcrLabelRunState, RunResultsResponse } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		const [run, settings] = await Promise.all([getRunResults(fetch, params.run_id), getSettings(fetch)]);
		let ocrLabelState: OcrLabelRunState = {
			run_id: params.run_id,
			labels: {
				false_rejection: [],
				false_acceptance: []
			}
		};
		if (settings.app.debug) {
			try {
				ocrLabelState = await getOcrLabelState(fetch, params.run_id);
			} catch {
				// Keep the run page usable even if the dev-only label-state route errors.
			}
		}
		return {
			run,
			runId: params.run_id,
			debugMode: settings.app.debug,
			ocrLabelState,
			error: null as string | null
		};
	} catch (e) {
		return {
			run: null as RunResultsResponse | null,
			runId: params.run_id,
			debugMode: false,
			ocrLabelState: {
				run_id: params.run_id,
				labels: {
					false_rejection: [],
					false_acceptance: []
				}
			} as OcrLabelRunState,
			error: e instanceof Error ? e.message : 'Failed to load run'
		};
	}
};
