<script lang="ts">
	import { Checkbox } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Badge, { type BadgeTone } from './Badge.svelte';
	import Icon from './Icon.svelte';
	import Progress from './Progress.svelte';

	/**
	 * Model components with selection, status, progress and size – used for initial
	 * setup and model settings.
	 *
	 * `auswahl`: checkbox for toggling; locked items stay checked (required).
	 * `laden`: status circle and progress per component.
	 */
	export interface Component {
		id: string;
		name: string;
		description: string;
		/** Display string like “350 MB”; caller formats the unit. */
		size: string;
		on: boolean;
		locked?: boolean;
		badge?: { text: string; tone: BadgeTone };
		status?: 'done' | 'loads' | 'waits' | 'skipped';
		/** 0–100 while loading. */
		progress?: number;
	}

	let {
		title,
		total,
		components,
		mode = 'selection',
		ontoggle,
		fuss,
		class: className
	}: {
		title: string;
		total?: string;
		components: readonly Component[];
		mode?: 'selection' | 'load';
		ontoggle?: (id: string) => void;
		fuss?: Snippet;
		class?: string;
	} = $props();

	const titleId = $props.id();

	function right(part: Component): { text: string; klass: string } {
		if (mode === 'selection') return { text: part.size, klass: 'text-muted' };
		switch (part.status) {
			case 'done':
				return {
					text: t('component.reviewed', { size: part.size }),
					klass: 'font-semibold text-green-ink'
				};
			case 'loads':
				return {
					text: t('component.loads', { percent: part.progress ?? 0, size: part.size }),
					klass: 'font-semibold text-link'
				};
			case 'skipped':
				return { text: t('component.skipped'), klass: 'text-subtle' };
			default:
				return { text: t('component.waits', { size: part.size }), klass: 'text-subtle' };
		}
	}
</script>

<section
	aria-labelledby={titleId}
	class={cn('flex flex-col overflow-hidden rounded-xl border border-line bg-surface', className)}
>
	<div class="flex items-baseline justify-between gap-3 border-b border-line-soft px-5 py-4">
		<h2 id={titleId} class="text-lead font-semibold">{title}</h2>
		{#if total}<span class="text-label text-muted">{total}</span>{/if}
	</div>
	<ul>
		{#each components as part (part.id)}
			{@const page = right(part)}
			<li class="flex items-center gap-4 border-b border-line-soft px-5 py-3.5">
				{#if mode === 'selection'}
					<Checkbox.Root
						checked={part.on || part.locked}
						disabled={part.locked}
						aria-label={part.name}
						onCheckedChange={() => ontoggle?.(part.id)}
						class={cn(
							'inline-flex size-6 shrink-0 items-center justify-center rounded-sm border-[1.5px] text-on-accent',
							part.locked
								? 'cursor-default border-faint bg-faint'
								: 'cursor-pointer border-line-dash bg-surface aria-checked:border-accent aria-checked:bg-accent'
						)}
					>
						{#snippet children({ checked })}
							{#if checked}<Icon name="haken" size={15} strokeWidth={2.6} />{/if}
						{/snippet}
					</Checkbox.Root>
				{:else}
					<span
						aria-hidden="true"
						class={cn(
							'inline-flex size-6 shrink-0 items-center justify-center rounded-full text-label font-bold',
							part.status === 'done' && 'bg-ink text-on-ink',
							part.status === 'loads' && 'bg-tint-2 text-link',
							(part.status === 'waits' || part.status === undefined) && 'bg-chip text-subtle',
							part.status === 'skipped' && 'bg-chip text-faint'
						)}
					>
						{#if part.status === 'done'}<Icon name="haken" size={14} strokeWidth={2.6} />
						{:else if part.status === 'loads'}<Icon name="save" size={14} strokeWidth={2.2} />
						{:else if part.status === 'skipped'}–
						{:else}·{/if}
					</span>
				{/if}
				<span class="flex min-w-0 flex-1 flex-col gap-0.75">
					<span class="flex flex-wrap items-center gap-2">
						<span class="text-lead font-semibold">{part.name}</span>
						{#if part.badge}<Badge tone={part.badge.tone} class="text-fine">{part.badge.text}</Badge
							>{/if}
					</span>
					<span class="text-label text-muted">{part.description}</span>
					{#if mode === 'load' && part.status === 'loads'}
						<Progress value={part.progress ?? 0} label={part.name} class="mt-1" />
					{/if}
				</span>
				<span class={cn('min-w-32.5 text-right text-label', page.klass)}>{page.text}</span>
			</li>
		{/each}
	</ul>
	{#if fuss}
		<div class="flex flex-wrap items-center justify-between gap-3 bg-surface-2 px-5 py-3.5">
			{@render fuss()}
		</div>
	{/if}
</section>
