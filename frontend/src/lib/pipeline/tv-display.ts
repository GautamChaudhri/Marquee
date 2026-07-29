import type { TvReviewGroup, TvRunQueueItem } from '$lib/api/types';

export type TvPosterTab = 'run' | 'review' | 'metrics';

export type TvPosterPreview = {
	label: string;
	url: string;
};

export type TvPosterPlaceholder = {
	label: string;
	title: string;
	year: number | null;
	gradientKey: string;
	centerTitle: boolean;
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

export function reviewPosterPreviews(item: TvReviewGroup): TvPosterPreview[] {
	const previews: TvPosterPreview[] = [];
	const showUrl = item.show_run?.auto_pick_poster_url;
	if (showUrl) previews.push({ url: showUrl, label: 'Show' });

	for (const seasonRun of [...item.season_runs].sort(
		(left, right) => left.season_number - right.season_number
	)) {
		if (!seasonRun.auto_pick_poster_url) continue;
		previews.push({
			url: seasonRun.auto_pick_poster_url,
			label: seasonLabel(seasonRun.season_number)
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
			gradientKey: item.series.title,
			centerTitle: false
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
			gradientKey: item.series.title,
			centerTitle: true
		});
	}
	return placeholders;
}
