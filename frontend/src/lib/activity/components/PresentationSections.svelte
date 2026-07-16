<script lang="ts">
	import type { PresentationSection } from '../types';
	import PresentationValue from './PresentationValue.svelte';

	let { sections }: { sections: PresentationSection[] } = $props();
</script>

<div class="sections">
	{#each sections as section, index (`${section.kind}-${index}`)}
		<section class={`section ${section.kind}`}>
			{#if 'title' in section && section.title}<h2>{section.title}</h2>{/if}

			{#if section.kind === 'facts'}
				<dl class="facts">
					{#each section.facts as fact (fact.label)}
						<div>
							<dt>{fact.label}</dt>
							<dd><PresentationValue value={fact.value} /></dd>
						</div>
					{/each}
				</dl>
			{:else if section.kind === 'metric_cards'}
				<div class="metrics">
					{#each section.cards as card (card.label)}
						<div>
							<span>{card.label}</span><strong><PresentationValue value={card.value} /></strong
							>{#if card.interpretation}<small>{card.interpretation}</small>{/if}
						</div>
					{/each}
				</div>
			{:else if section.kind === 'before_after'}
				<div class="rows">
					{#each section.rows as row (row.label)}
						<div class:changed={row.changed}>
							<strong>{row.label}</strong><PresentationValue value={row.before} /><span
								aria-hidden="true">→</span
							><PresentationValue value={row.after} />
						</div>
					{/each}
				</div>
			{:else if section.kind === 'change_list'}
				<ul>
					{#each section.items as item (`${item.target_key}-${item.stage ?? ''}`)}<li>
							<strong>{item.target_label}</strong> — {item.requested}: {item.outcome}{#if item.reason}<span
								>
									· {item.reason}</span
								>{/if}
						</li>{/each}
				</ul>
			{:else if section.kind === 'track_table'}
				<div class="table-wrap">
					<table>
						<thead
							><tr
								><th>Kind</th><th>Language</th><th>Codec</th><th>Title</th><th>Flags</th><th
									>Outcome</th
								></tr
							></thead
						><tbody
							>{#each section.tracks as track, i (i)}<tr
									><td>{track.track_kind}</td><td>{track.language}</td><td>{track.codec ?? '—'}</td
									><td>{track.title ?? '—'}</td><td
										>{[
											track.is_default && 'default',
											track.is_forced && 'forced',
											track.is_sdh && 'SDH',
											track.is_commentary && 'commentary'
										]
											.filter(Boolean)
											.join(', ') || '—'}</td
									><td>{track.outcome ?? '—'}{track.reason ? ` · ${track.reason}` : ''}</td></tr
								>{/each}</tbody
						>
					</table>
				</div>
			{:else if section.kind === 'steps'}
				<ol class="steps">
					{#each section.steps as step (step.key)}<li class={step.state}>
							<strong>{step.label}</strong><span>{step.state}</span>{#if step.at}<time
									datetime={step.at}>{new Date(step.at).toLocaleString()}</time
								>{/if}
						</li>{/each}
				</ol>
			{:else if section.kind === 'artifacts'}
				<ul>
					{#each section.items as item (`${item.artifact_kind}-${item.name}`)}<li>
							<strong>{item.name}</strong> · {item.artifact_kind} · {item.status}{#if item.link}<span
								>
									· <a href={item.link.href}>{item.link.label}</a></span
								>{/if}
						</li>{/each}
				</ul>
			{:else if section.kind === 'children'}
				<div class="child-summary">
					<strong>{section.total} child jobs</strong><span
						>{section.running} running · {section.queued} queued · {section.succeeded} succeeded · {section.failed}
						failed · {section.cancelled} cancelled · {section.no_change} no change</span
					><a href={section.children_link.href}>{section.children_link.label}</a>
				</div>
			{:else if section.kind === 'warnings'}
				<ul class="warnings">
					{#each section.items as item, i (i)}<li>
							{item.message}{item.code ? ` (${item.code})` : ''}
						</li>{/each}
				</ul>
			{:else if section.kind === 'failures'}
				<ul class="failures">
					{#each section.items as item, i (i)}<li>
							<strong>{item.message}</strong>{#if item.remediation}<span>{item.remediation}</span
								>{/if}
						</li>{/each}
				</ul>
			{:else if section.kind === 'notice'}
				<p class={`notice ${section.tone}`}>{section.message}</p>
			{/if}
		</section>
	{/each}
</div>

<style>
	.sections {
		display: grid;
		gap: 16px;
	}
	.section {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
	}
	h2 {
		margin: 0 0 12px;
		font-size: 14px;
	}
	.facts {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 12px;
		margin: 0;
	}
	.facts div {
		min-width: 0;
	}
	dt,
	.metrics span {
		color: var(--muted);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	dd {
		margin: 3px 0 0;
		overflow-wrap: anywhere;
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 10px;
	}
	.metrics > div {
		display: grid;
		gap: 3px;
		background: var(--panel2);
		border-radius: 8px;
		padding: 12px;
	}
	.metrics strong {
		font-size: 18px;
	}
	.metrics small {
		color: var(--muted);
	}
	.rows {
		display: grid;
		gap: 8px;
	}
	.rows > div {
		display: grid;
		grid-template-columns: minmax(120px, 1fr) 1fr auto 1fr;
		gap: 10px;
		align-items: center;
	}
	.rows .changed {
		color: var(--gold);
	}
	ul {
		margin: 0;
		padding-left: 20px;
		display: grid;
		gap: 7px;
	}
	li span,
	.child-summary span {
		color: var(--muted);
	}
	a {
		color: var(--gold);
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	th,
	td {
		text-align: left;
		padding: 8px;
		border-bottom: 1px solid var(--line2);
	}
	th {
		color: var(--faint2);
		font-size: 10px;
		text-transform: uppercase;
	}
	.steps {
		list-style: none;
		padding: 0;
	}
	.steps li {
		display: grid;
		grid-template-columns: 1fr auto auto;
		gap: 12px;
	}
	.steps li > span,
	time {
		color: var(--muted);
		font-size: 12px;
	}
	.steps .failed {
		color: var(--bad);
	}
	.child-summary {
		display: grid;
		gap: 8px;
	}
	.failures,
	.warnings {
		list-style: none;
		padding: 0;
	}
	.failures li {
		display: grid;
		gap: 4px;
		color: var(--bad);
	}
	.warnings {
		color: var(--warn);
	}
	.notice {
		margin: 0;
		border-left: 3px solid var(--info);
		padding-left: 12px;
	}
	.notice.warning {
		border-color: var(--warn);
	}
	.notice.success {
		border-color: var(--good);
	}
	@media (max-width: 640px) {
		.rows > div {
			grid-template-columns: 1fr;
			border-bottom: 1px solid var(--line2);
			padding-bottom: 8px;
		}
	}
</style>
