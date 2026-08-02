export type PosterReviewMediaType = 'movie' | 'series' | 'season' | undefined;

export type RejectAllCopy = {
	title: string;
	message: string;
};

export type ReviewModeCopy = {
	tabLabel: string;
	guidance: string | null;
};

export type ReviewCandidateSummary = {
	label: string;
	tone: 'good' | 'warn' | 'bad';
};

export type ReviewResultsNotice = {
	tone: 'warn' | 'bad';
	title: string;
	message: string;
};

export function reviewModeCopy(
	mode: 'collecting' | 'personalized' | undefined,
	mediaType?: 'movie' | 'series' | 'season'
): ReviewModeCopy {
	if (mode === 'collecting') {
		const library = mediaType === 'movie' ? 'Movies' : 'Television';
		return {
			tabLabel: 'Review candidates',
			guidance: `These candidates passed objective checks. Their display order is neutral, not a recommendation. Choose one to teach ${library} taste.`
		};
	}

	return { tabLabel: 'Ranked', guidance: null };
}

/**
 * The reviewed survivor list is the source of truth for this state—not a
 * stored count and never the presence of an auto-pick. A run with no survivors
 * needs recovery guidance, while a cold-start run with survivors needs a
 * manual-review explanation without pretending that its neutral order is a
 * recommendation.
 */
export function reviewResultsNotice(
	mode: 'collecting' | 'personalized' | undefined,
	survivorCount: number,
	mediaType?: PosterReviewMediaType
): ReviewResultsNotice | null {
	if (survivorCount === 0) {
		return {
			tone: 'bad',
			title: 'No survivors',
			message: 'No candidates survived the objective checks. Re-run this title to try again.'
		};
	}

	if (mode !== 'collecting') return null;
	return {
		tone: 'warn',
		title: 'Manual review',
		message: reviewModeCopy(mode, mediaType).guidance ?? ''
	};
}

/** Format the compact age used by Film cards and each TV asset preview. */
export function relativeAge(iso: string | null, now = Date.now()): string {
	if (!iso) return '—';
	const then = new Date(iso).getTime();
	if (Number.isNaN(then)) return '—';
	const seconds = Math.max(0, (now - then) / 1000);
	if (seconds < 60) return 'just now';
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
	if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
	return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * Keep the compact review-card status honest: a persisted auto-pick is the
 * only evidence that candidates were personalized, while cold-start runs are
 * deliberately neutral. A zero-candidate run is actionable regardless of its
 * scorer state.
 */
export function reviewCandidateSummary(
	candidateCount: number | null | undefined,
	hasPersistedAutoPick: boolean
): ReviewCandidateSummary {
	const count = Math.max(0, Math.trunc(candidateCount ?? 0));
	return {
		label: `${count} candidate${count === 1 ? '' : 's'}`,
		tone: count === 0 ? 'bad' : hasPersistedAutoPick ? 'good' : 'warn'
	};
}

export function rejectAllCopy({
	mediaType,
	seasonNumber
}: {
	mediaType: PosterReviewMediaType;
	seasonNumber?: number | null;
}): RejectAllCopy {
	if (mediaType === 'series') {
		return {
			title: 'Reject all show poster candidates',
			message:
				'Records a negative label for the auto-pick and leaves this TV show without a chosen show poster. You can re-run later.'
		};
	}

	if (mediaType === 'season') {
		const subject =
			seasonNumber == null ? "this TV show's season" : `Season ${seasonNumber} of this TV show`;
		return {
			title: 'Reject all season poster candidates',
			message: `Records a negative label for the auto-pick and leaves ${subject} without a chosen season poster. You can re-run later.`
		};
	}

	return {
		title: 'Reject all candidates',
		message:
			'Records a negative label for the auto-pick and leaves this movie without a chosen poster. You can re-run later.'
	};
}
