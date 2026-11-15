<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import FileBadge from '#lib/ui/FileBadge.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';

	/**
	 * The tabs above the document (back to the overview),
	 * one tab per file with a badge and a count of open questions,
	 * “+” to add more documents. Scroll horizontally when many files.
	 *
	 * Keyboard follows normal tab behavior: arrow keys move and select,
	 * Tab leaves the row; only the selected tab is focusable.
	 */
	let {
		files,
		active,
		back,
		panelId,
		addLocked = false,
		onselect,
		onadd
	}: {
		files: readonly { id: string; name: string; open: number }[];
		active: number;
		back: string;
		/** ID of the document area controlled by the selected tab. */
		panelId: string;
		addLocked?: boolean;
		onselect: (index: number) => void;
		onadd: () => void;
	} = $props();

	let row = $state<HTMLElement>();

	function hotkey(event: KeyboardEvent, index: number): void {
		const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
		const target =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? files.length - 1
					: step !== undefined
						? (index + step + files.length) % files.length
						: null;
		if (target === null) return;
		event.preventDefault();
		onselect(target);
		row?.querySelectorAll<HTMLElement>('[role="tab"]')[target]?.focus();
	}

	$effect(() => {
		row
			?.querySelectorAll<HTMLElement>('[role="tab"]')
			[active]?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
	});
</script>

<div
	class="pointer-events-auto flex min-w-0 flex-[0_1_auto] items-center gap-0.5 rounded-[13px] border border-line bg-glass p-1 shadow-float backdrop-blur-md"
>
	<IconButton href={back} label={t('review.toTheOverview')} icon="back" size="sm" />
	<span aria-hidden="true" class="mx-1 h-5 w-px shrink-0 bg-line"></span>
	<div
		bind:this={row}
		role="tablist"
		aria-label={t('review.files')}
		class="flex min-w-0 [scrollbar-width:none] items-center gap-0.5 overflow-x-auto"
	>
		{#each files as file, index (file.id)}
			{@const selected = index === active}
			<button
				type="button"
				role="tab"
				id={`reiter-${file.id}`}
				aria-selected={selected}
				aria-controls={selected ? panelId : undefined}
				tabindex={selected ? 0 : -1}
				onclick={() => onselect(index)}
				onkeydown={(event) => hotkey(event, index)}
				class={cn(
					'inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.75 rounded-[9px] border px-2.5 text-label whitespace-nowrap',
					selected
						? 'border-line-strong bg-surface font-semibold text-ink'
						: 'border-transparent text-ink-2 hover:bg-surface-2'
				)}
			>
				<FileBadge name={file.name} size="small" />
				<span>{file.name}</span>
				{#if file.open > 0}
					<span
						class="inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-amber-strong px-1.25 text-fine font-bold text-on-amber-strong"
						><span class="sr-only">{t('review.questionsOpen', { n: file.open })}: </span><span
							aria-hidden="true">{file.open}</span
						></span
					>
				{/if}
			</button>
		{/each}
	</div>
	<IconButton
		label={t('review.further')}
		icon="new"
		variant="link"
		size="sm"
		disabled={addLocked}
		onclick={onadd}
	/>
</div>
