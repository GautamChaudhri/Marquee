import type { TvReviewGroup, TvRunQueueItem } from '$lib/api/types';
import {
	relativeAge,
	reviewCandidateSummary,
	type ReviewCandidateSummary
} from '$lib/pipeline/review-copy';

export type TvPosterTab = 'run' | 'review' | 'metrics';

export type TvPosterPreview = {
	label: string;
	/** null when the run needs manual review or produced no survivors. */
	url: string | null;
	candidateSummary: ReviewCandidateSummary;
	age: string;
};

export type TvPosterPlaceholder = {
	label: string;
	title: string;
	year: number | null;
	gradientKey: string;
};

export function seasonLabel(number: number): string {
	return number === 0 ? 'S00' : `S${String(number).padStart(2, '0')}`;
}

export function resolveTvPosterTab(
	requestedTab: string | null,
	runTotal: number,
	reviewTotal: number
): TvPosterTab {
	if (requestedTab === 'run' || requestedTab === 'review' || requestedTab === 'metrics') {
		return requestedTab;
	}
	return runTotal === 0 && reviewTotal > 0 ? 'review' : 'run';
}

/**
 * One tile per run awaiting review. Runs that ended `flagged_manual` have no auto
 * pick, and they still get a labelled tile — dropping them left the card showing
 * an unlabelled poster that was never a candidate for the run it stood in for.
 */
export function reviewPosterPreviews(item: TvReviewGroup, now = Date.now()): TvPosterPreview[] {
	const previews: TvPosterPreview[] = [];
	if (item.show_run) {
		const url = item.show_run.auto_pick_poster_url ?? null;
		previews.push({
			url,
			label: 'Show',
			candidateSummary: reviewCandidateSummary(item.show_run.counts?.ranked, Boolean(url)),
			age: relativeAge(item.show_run.started_at, now)
		});
	}

	for (const seasonRun of [...item.season_runs].sort(
		(left, right) => left.season_number - right.season_number
	)) {
		const url = seasonRun.auto_pick_poster_url ?? null;
		previews.push({
			url,
			label: seasonLabel(seasonRun.season_number),
			candidateSummary: reviewCandidateSummary(seasonRun.run.counts?.ranked, Boolean(url)),
			age: relativeAge(seasonRun.run.started_at, now)
		});
	}
	return previews;
}

export function runPosterPlaceholders(item: TvRunQueueItem): TvPosterPlaceholder[] {
	const placeholders: TvPosterPlaceholder[] = [];
	if (item.show_poster_missing) {
		placeholders.push({
			label: 'Show',
			title: item.series.title,
			year: item.series.year,
			gradientKey: item.series.title
		});
	}

	for (const season of [...item.missing_seasons].sort(
		(left, right) => left.number - right.number
	)) {
		const label = seasonLabel(season.number);
		placeholders.push({
			label,
			title: label,
			year: null,
			gradientKey: item.series.title
		});
	}
	return placeholders;
}
