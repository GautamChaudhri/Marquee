import type { PageLoad } from './$types';
import { getPipelineSummary } from '$lib/api/pipeline';
import { getTvSummary } from '$lib/api/pipeline-tv';
import { getSettings } from '$lib/api/system';
import { listTextProfiles, type ScopedTextProfileList } from '$lib/api/text-profiles';
import type { PipelineSummary, RuntimeSettings, TvPipelineSummary } from '$lib/api/types';

const EMPTY_SUMMARY: PipelineSummary = {
	total_movies: 0,
	movies_with_poster: 0,
	movies_missing_poster: 0,
	movies_awaiting_run: 0,
	movies_in_review: 0,
	movies_in_run: 0,
	running_jobs: [],
	last_heal: null,
	heal_schedule: null,
	backups: { count: 0, bytes: 0 }
};

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	const [summary, tvSummary, settings, textProfiles] = await Promise.all([
		safe<PipelineSummary>(getPipelineSummary(fetch), EMPTY_SUMMARY),
		safe<TvPipelineSummary | null>(getTvSummary(fetch), null),
		safe<RuntimeSettings | null>(getSettings(fetch), null),
		safe<ScopedTextProfileList | null>(listTextProfiles(fetch), null)
	]);
	return { summary, tvSummary, settings, textProfiles };
};
