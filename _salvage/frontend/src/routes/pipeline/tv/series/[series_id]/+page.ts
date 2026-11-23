import type { PageLoad } from './$types';
import { getSeries } from '$lib/api/library';
import { getSeriesRuns, getTvReviewQueue } from '$lib/api/pipeline-tv';
import { getOcrLabelState, getRunResults } from '$lib/api/pipeline';
import { getSettings } from '$lib/api/system';
import type { OcrLabelRunState, RunResultsResponse } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, params, url }) => {
	const seriesId = Number(params.series_id);
	const selectedRunId = url.searchParams.get('run');
	try {
		const [series, reviewQueue, runsResponse, settings] = await Promise.all([
			getSeries(fetch, seriesId),
			getTvReviewQueue(fetch, { page_size: 200 }).catch(() => null),
			getSeriesRuns(fetch, seriesId),
			getSettings(fetch)
		]);
		const reviewGroup = reviewQueue?.items.find((item) => item.series.id === seriesId) ?? null;
		const selectedRun =
			selectedRunId ??
			reviewGroup?.show_run?.run_id ??
			reviewGroup?.season_runs[0]?.run.run_id ??
			runsResponse.runs[0]?.run_id ??
			null;
		let run: RunResultsResponse | null = null;
		let ocrLabelState: OcrLabelRunState = {
			run_id: selectedRun ?? '',
			labels: { false_rejection: [], false_acceptance: [] }
		};
		if (selectedRun) {
			run = await getRunResults(fetch, selectedRun);
			if (settings.app.debug) {
				try {
					ocrLabelState = await getOcrLabelState(fetch, selectedRun);
				} catch {
					/* keep usable */
				}
			}
		}
		return {
			series,
			reviewGroup,
			runs: runsResponse.runs,
			selectedRunId: selectedRun,
			runData: {
				run,
				runId: selectedRun ?? '',
				debugMode: settings.app.debug,
				ocrLabelState,
				error: null as string | null
			},
			error: null as string | null
		};
	} catch (e) {
		return {
			series: null,
			reviewGroup: null,
			runs: [],
			selectedRunId: null as string | null,
			runData: {
				run: null as RunResultsResponse | null,
				runId: '',
				debugMode: false,
				ocrLabelState: {
					run_id: '',
					labels: { false_rejection: [], false_acceptance: [] }
				},
				error: e instanceof Error ? e.message : 'Failed to load series review'
			},
			error: e instanceof Error ? e.message : 'Failed to load series review'
		};
	}
};
