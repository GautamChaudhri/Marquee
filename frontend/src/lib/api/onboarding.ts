import { apiGet, apiSend, type Fetch } from './client';
import type { JobSubmissionResponse } from '$lib/activity/types';
import type { OnboardingStatus, TasteTestMovie } from './types';

/** Cold-start onboarding status: data state + Rank Test progress. */
export function getOnboardingStatus(fetchFn: Fetch): Promise<OnboardingStatus> {
	return apiGet<OnboardingStatus>(fetchFn, '/onboarding/status');
}

interface StartResult {
	path: 'library' | 'taste_test';
	status: OnboardingStatus;
	movies?: TasteTestMovie[];
	sample_movie_ids?: number[];
}

/** Begin the Rank Test; path null = auto-detect (library if present, else test). */
export function startOnboarding(
	fetchFn: Fetch,
	path: 'library' | 'taste_test' | null = null
): Promise<StartResult> {
	return apiSend<StartResult>(fetchFn, 'POST', '/onboarding/start', { path });
}

export function getTasteTestMovies(fetchFn: Fetch): Promise<{ movies: TasteTestMovie[] }> {
	return apiGet<{ movies: TasteTestMovie[] }>(fetchFn, '/onboarding/taste-test/movies');
}

/** Rank one bundled taste-test movie. */
export function tasteTestRank(
	fetchFn: Fetch,
	movieId: string,
	order: string[],
	hated: string[]
): Promise<{ status: OnboardingStatus }> {
	return apiSend(fetchFn, 'POST', '/onboarding/taste-test/rank', {
		movie_id: movieId,
		order,
		hated
	});
}

/** Mark the Rank Test complete (≥min) → rebuild profile + train head. */
export function completeOnboarding(fetchFn: Fetch): Promise<{
	status: OnboardingStatus;
	rebuild_job: JobSubmissionResponse;
	head_job: JobSubmissionResponse;
}> {
	return apiSend(fetchFn, 'POST', '/onboarding/complete', {});
}
