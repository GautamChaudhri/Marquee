import type { PageLoad } from './$types';
import { getSeries } from '$lib/api/library';
import { getSeriesArtworkEvents, getSeriesRuns } from '$lib/api/pipeline-tv';
import { getSeriesTextProfiles, listTextProfiles } from '$lib/api/text-profiles';

export const load: PageLoad = async ({ fetch, params }) => {
	const id = Number(params.id);
	if (!id) {
		return {
			series: null,
			textProfiles: null,
			seriesProfiles: null,
			runs: [],
			events: [],
			error: 'Invalid series id'
		};
	}

	try {
		const series = await getSeries(fetch, id);
		let textProfiles = null;
		let seriesProfiles = null;
		let runs: Awaited<ReturnType<typeof getSeriesRuns>>['runs'] = [];
		let events: Awaited<ReturnType<typeof getSeriesArtworkEvents>>['events'] = [];
		try {
			[textProfiles, seriesProfiles, runs, events] = await Promise.all([
				listTextProfiles(fetch),
				getSeriesTextProfiles(fetch, id),
				getSeriesRuns(fetch, id).then((value) => value.runs),
				getSeriesArtworkEvents(fetch, id).then((value) => value.events)
			]);
		} catch {
			/* keep detail page usable without auxiliary panels */
		}
		return { series, textProfiles, seriesProfiles, runs, events, error: null as string | null };
	} catch (e) {
		return {
			series: null,
			textProfiles: null,
			seriesProfiles: null,
			runs: [],
			events: [],
			error: e instanceof Error ? e.message : 'Failed to load series'
		};
	}
};
