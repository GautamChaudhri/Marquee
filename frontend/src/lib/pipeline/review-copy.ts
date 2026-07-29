export type PosterReviewMediaType = 'movie' | 'series' | 'season' | undefined;

export type RejectAllCopy = {
	title: string;
	message: string;
};

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
