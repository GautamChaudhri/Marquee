import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const directory = dirname(fileURLToPath(import.meta.url));
const sources = readdirSync(directory)
	.filter((name) => name.endsWith('.svelte'))
	.map((name) => [name, readFileSync(join(directory, name), 'utf8')] as const);
const combined = sources.map(([, source]) => source).join('\n');

describe('shared activity component source contract', () => {
	it('never renders or humanizes raw stage keys', () => {
		expect(combined).not.toContain('stage_key');
		expect(combined).not.toMatch(/replace(All)?\([^\n]*[_-]/);
		expect(combined).not.toMatch(/humaniz/i);
	});

	it('never calculates a client percentage', () => {
		expect(combined).not.toMatch(/percent\s*[*+/-]/);
		expect(combined).not.toMatch(/[*+/]\s*100/);
		expect(combined).not.toMatch(/Math\.[A-Za-z]+\([^\n]*percent/);

		const progressSource = sources.find(([name]) => name === 'ProgressMeasure.svelte')?.[1];
		expect(progressSource).toContain('aria-valuenow={measurement.percent}');
		expect(progressSource).toContain('style:width={`${measurement.percent}%`}');
	});

	it('uses the Activity status palette consistently', () => {
		const progressSource = sources.find(([name]) => name === 'ProgressMeasure.svelte')?.[1];
		const attentionSource = sources.find(([name]) => name === 'ActivityAttentionStrip.svelte')?.[1];
		expect(progressSource).toContain('background: var(--fill-color, var(--info))');
		expect(progressSource).toContain(".measurement[data-tone='warning']");
		expect(attentionSource).toContain("label: 'In Queue'");
		expect(attentionSource).toContain("tone: 'running'");
		expect(attentionSource).toContain("tone: 'retrying'");
		expect(attentionSource).toContain("tone: 'attention'");
	});

	it('does not import a legacy job tracker or handwritten wire client', () => {
		expect(combined).not.toMatch(/lib\/api\/(jobs|media-jobs)/);
		expect(combined).not.toMatch(/lib\/jobs/);
		for (const [name, source] of sources) {
			expect(source, name).not.toMatch(/localStorage|sessionStorage/);
		}
	});
});
