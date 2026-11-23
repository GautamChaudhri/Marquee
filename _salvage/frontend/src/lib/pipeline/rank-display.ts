import type { CandidateView } from '$lib/api/types';

type FlatGridItem = CandidateView | CandidateView[];

export type FlatDisplayRank = {
	rank: number;
	suffix: string | null;
};

function alphabeticalLabel(position: number): string {
	let label = '';
	let remaining = position;
	while (remaining > 0) {
		remaining -= 1;
		label = String.fromCharCode(65 + (remaining % 26)) + label;
		remaining = Math.floor(remaining / 26);
	}
	return label;
}

function stackVariantLabel(candidate: CandidateView, fallbackPosition: number): string {
	const label = candidate.stack_label?.trim();
	if (label && /^[a-z]+$/i.test(label)) return label.toUpperCase();
	return alphabeticalLabel(candidate.stack_pos ?? fallbackPosition);
}

/**
 * Assign ranks to the cards in their actual flat-grid order.
 *
 * A collapsed design stack consumes one visible rank. When it is expanded,
 * every variant retains that base rank and gains an A/B/C… suffix instead of
 * shifting the rank of the cards that follow it.
 */
export function flatDisplayRanks(
	items: readonly FlatGridItem[],
	expandedStackIds: readonly number[]
): Map<string, FlatDisplayRank> {
	const expanded = new Set(expandedStackIds);
	const ranks = new Map<string, FlatDisplayRank>();

	for (const [index, item] of items.entries()) {
		const rank = index + 1;
		if (!Array.isArray(item)) {
			ranks.set(item.orig_filename, { rank, suffix: null });
			continue;
		}

		const stackId = item[0]?.stack_id;
		const isExpanded = stackId != null && expanded.has(stackId);
		const visibleMembers = isExpanded ? item : item.slice(0, 1);
		for (const [variantIndex, candidate] of visibleMembers.entries()) {
			ranks.set(candidate.orig_filename, {
				rank,
				suffix: isExpanded ? stackVariantLabel(candidate, variantIndex + 1) : null
			});
		}
	}

	return ranks;
}

export function rankCaption(rank: number | null | undefined, suffix: string | null = null): string {
	return rank == null ? '' : `Rank ${rank}${suffix ?? ''}`;
}
