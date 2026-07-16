<script lang="ts">
	import type { JobRow } from '../types';

	let { children, hasMore = false }: { children: JobRow[]; hasMore?: boolean } = $props();

	const visible = $derived(children.slice(0, 3));
	const remaining = $derived(
		children.length > visible.length ? children.length - visible.length : 0
	);
</script>

{#if visible.length > 0}
	<section class="concurrent" aria-label="Concurrent work">
		<strong>Also in progress</strong>
		<ul>
			{#each visible as child (child.job_id)}
				<li>
					<span>{child.subject.display_name}</span>
					<small>{child.status.label}</small>
				</li>
			{/each}
		</ul>
		{#if remaining > 0 || hasMore}
			<p>{remaining > 0 ? `${remaining} more loaded` : 'More activity available'}</p>
		{/if}
	</section>
{/if}

<style>
	.concurrent {
		display: grid;
		gap: 7px;
	}
	.concurrent > strong {
		font-size: 12px;
	}
	ul {
		display: grid;
		gap: 5px;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding-left: 10px;
		border-left: 2px solid var(--line2);
	}
	li span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	li small,
	p {
		color: var(--muted);
	}
	p {
		margin: 0;
		font-size: 11px;
	}
</style>
