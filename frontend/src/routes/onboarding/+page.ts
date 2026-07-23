import type { PageLoad } from './$types';
import { getOnboardingReview, getOnboardingStatus } from '$lib/api/onboarding';
import type { OnboardingReview, OnboardingStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	let status: OnboardingStatus | null = null;
	let error: string | null = null;
	let review: OnboardingReview | null = null;
	let reviewError: string | null = null;
	try {
		status = await getOnboardingStatus(fetch);
	} catch (e) {
		error = e instanceof Error ? e.message : 'Could not load onboarding status';
	}
	const runId = url.searchParams.get('review') ?? status?.review?.run_id ?? null;
	if (runId) {
		try {
			review = await getOnboardingReview(fetch, runId);
		} catch (e) {
			reviewError = e instanceof Error ? e.message : 'Could not load the candidate review';
		}
	}
	return { status, error, review, reviewError };
};
