<script lang="ts">
	import type { Exemplar, ExemplarChanges } from '$lib/api/types';

	/** What an answer or an undo did to the exemplars: made, ended, and brought back. */
	let { changes }: { changes: ExemplarChanges } = $props();

	const groups = $derived(
		[
			{ label: 'Made', items: changes.created },
			{ label: 'Ended', items: changes.ended },
			{ label: 'Brought back', items: changes.reactivated }
		].filter((group) => group.items.length > 0)
	);

	function describe(exemplar: Exemplar): string {
		const what = exemplar.polarity === 'positive' ? 'liked' : 'disliked';
		const file = exemplar.source_file ?? 'the seed poster';
		return `${file}, ${what}, weight ${exemplar.weight.toFixed(2)}`;
	}
</script>

{#if groups.length === 0}
	<p class="none">No exemplar changed.</p>
{:else}
	<dl class="changes" data-testid="exemplar-changes">
		{#each groups as group (group.label)}
			<dt>{group.label}</dt>
			<dd>
				<ul>
					{#each group.items as exemplar (exemplar.id)}
						<li>
							<img src={exemplar.image_url} alt="" width="500" height="750" loading="lazy" />
							<span>{describe(exemplar)}</span>
						</li>
					{/each}
				</ul>
			</dd>
		{/each}
	</dl>
{/if}

<style>
	.none {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.changes {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 6px 12px;
		margin: 0;
		font-size: 12.5px;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	li {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}
	li span {
		overflow-wrap: anywhere;
	}
	img {
		width: 28px;
		height: 42px;
		object-fit: cover;
		border-radius: 3px;
		flex: none;
		background: var(--panel2);
	}
</style>
