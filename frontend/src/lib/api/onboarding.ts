import { apiGet, apiSend, type Fetch } from './client';
import type { JobSubmissionResponse } from '$lib/activity/types';
import type { OnboardingStatus } from './types';

/** Cold-start onboarding status: data state + Rank Test progress. */
export function getOnboardingStatus(fetchFn: Fetch): Promise<OnboardingStatus> {
	return apiGet<OnboardingStatus>(fetchFn, '/onboarding/status');
}

interface StartResult {
	subject: { kind: 'movie'; id: number; title: string; year: number | null };
	analysis_job: JobSubmissionResponse;
	status: OnboardingStatus;
}

/** Submit profile-independent analysis for the next unconfirmed movie. */
export function startOnboarding(fetchFn: Fetch): Promise<StartResult> {
	return apiSend<StartResult>(fetchFn, 'POST', '/onboarding/start', {});
}

/** Build both native taste profiles from the exact eligible evidence revision. */
export function completeOnboarding(fetchFn: Fetch): Promise<{
	status: OnboardingStatus;
	revision: string;
	build_jobs: JobSubmissionResponse[];
}> {
	return apiSend(fetchFn, 'POST', '/onboarding/complete', {});
}
