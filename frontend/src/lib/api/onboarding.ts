import { apiGet, apiSend, type Fetch } from './client';
import type { JobSubmissionResponse } from '$lib/activity/types';
import type { OnboardingDecision, OnboardingReview, OnboardingStatus } from './types';

/** Cold-start onboarding status: canonical evidence and profile-build progress. */
export function getOnboardingStatus(
	fetchFn: Fetch,
	signal?: AbortSignal
): Promise<OnboardingStatus> {
	return apiGet<OnboardingStatus>(fetchFn, '/onboarding/status', undefined, signal);
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

/** Load a neutral candidate review whose membership and evidence stay server-owned. */
export function getOnboardingReview(
	fetchFn: Fetch,
	runId: string,
	signal?: AbortSignal
): Promise<OnboardingReview> {
	return apiGet<OnboardingReview>(
		fetchFn,
		`/onboarding/runs/${encodeURIComponent(runId)}/review`,
		undefined,
		signal
	);
}

type OnboardingDecisionIntent = {
	run_id: string;
	candidate_id: string;
	review_revision: string;
	idempotency_key: string;
};

/** Submit one canonical review choice; the browser contributes intent only. */
export function chooseOnboardingCandidate(
	fetchFn: Fetch,
	intent: OnboardingDecisionIntent
): Promise<OnboardingDecision> {
	return apiSend<OnboardingDecision>(fetchFn, 'POST', '/onboarding/choose', intent);
}

/** Record explicit dislike without triggering any poster deployment. */
export function hateOnboardingCandidate(
	fetchFn: Fetch,
	intent: OnboardingDecisionIntent
): Promise<OnboardingDecision> {
	return apiSend<OnboardingDecision>(fetchFn, 'POST', '/onboarding/hate', intent);
}

/** Build both native taste profiles from the exact eligible evidence revision. */
export function completeOnboarding(fetchFn: Fetch): Promise<{
	status: OnboardingStatus;
	revision: string;
	build_jobs: JobSubmissionResponse[];
}> {
	return apiSend(fetchFn, 'POST', '/onboarding/complete', {});
}
