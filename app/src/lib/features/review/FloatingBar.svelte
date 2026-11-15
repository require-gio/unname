<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Popover from '#lib/ui/Popover.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import type { IconName } from '#lib/ui/icons.js';
	import { ZOOM_LEVELS, zoomStep, type ZoomLevel } from './zoom.js';

	/**
	 * Dark floating bar below the document:
	 * result · side-by-side · original, masking menu with kind and count, zoom.
	 * Stays dark in both modes.
	 *
	 * For fixed pages a “page width” toggle is added. Clicking the zoom display
	 * fills the column; −/+ returns to the fixed steps.
	 */
	export type View = 'result' | 'sideBySide' | 'original';

	let {
		view = $bindable(),
		zoom = $bindable(),
		pageWidth = $bindable(false),
		pageWidthPossible = false,
		menuOpen = $bindable(false),
		kind,
		kindIcon,
		places,
		menu,
		bar = $bindable()
	}: {
		view: View;
		zoom: ZoomLevel;
		pageWidth?: boolean;
		/** Only fixed pages have a “page width” toggle. */
		pageWidthPossible?: boolean;
		menuOpen?: boolean;
		/** Selected kind: Reversible, Irreversible or “mask all”. */
		kind: string;
		kindIcon: IconName;
		places: number;
		/** Content of the masking menu. */
		menu: Snippet;
		bar?: HTMLElement;
	} = $props();

	const options = $derived([
		{
			value: 'result' as const,
			label: t('review.view.result'),
			title: t('review.view.result.title'),
			icon: 'badge' as const
		},
		{
			value: 'sideBySide' as const,
			label: t('review.view.sideBySide'),
			title: t('review.view.sideBySide.title'),
			icon: 'sideBySide' as const
		},
		{
			value: 'original' as const,
			label: t('review.view.original'),
			title: t('review.view.original.title'),
			icon: 'document' as const
		}
	]);

	const wide = $derived(pageWidthPossible && pageWidth);

	function level(direction: 1 | -1): void {
		if (wide) {
			pageWidth = false;
			return;
		}
		zoom = zoomStep(zoom, direction);
	}
</script>

<div
	bind:this={bar}
	role="toolbar"
	aria-label={t('review.bar')}
	class="absolute bottom-5 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1 rounded-[16px] bg-bar p-1.5 font-sans whitespace-nowrap text-bar-ink shadow-bar"
>
	<Segmented
		variant="bar"
		label={t('review.view')}
		{options}
		bind:value={view}
		onlyActiveBeschriftet
	/>
	<span aria-hidden="true" class="mx-1 h-5.5 w-px bg-bar-line"></span>
	<Popover
		bind:open={menuOpen}
		label={t('masking.title')}
		page="top"
		alignment="center"
		gap={14}
		class="w-[468px] gap-4 rounded-xl"
	>
		{#snippet trigger(props)}
			<button
				{...props}
				type="button"
				aria-label={t('review.masking.button', {
					kind,
					places: t('code.places', { n: places })
				})}
				class="inline-flex h-9.5 cursor-pointer items-center gap-2 rounded-[11px] bg-bar-raised px-3 text-label text-bar-ink hover:bg-bar-on/15"
				{@attach coachmark(3, 'help.review.menu', { page: 'top' })}
			>
				<span class="text-bar-muted">{t('masking.title')}</span>
				<Icon name={kindIcon} size={15} strokeWidth={1.9} />
				<span class="font-semibold">{kind}</span>
				<span
					class="rounded-full bg-bar-on/15 px-1.75 py-px text-fine font-semibold text-bar-ink tabular-nums"
					>{places}</span
				>
				<Icon
					name="zuklappen"
					size={14}
					strokeWidth={2.2}
					class={cn('transition-transform', menuOpen && 'rotate-180')}
				/>
			</button>
		{/snippet}
		{@render menu()}
	</Popover>
	<span aria-hidden="true" class="mx-1 h-5.5 w-px bg-bar-line"></span>
	<IconButton
		variant="bar"
		label={t('review.zoom.small')}
		icon="small"
		disabled={!wide && zoom === ZOOM_LEVELS[0]}
		onclick={() => level(-1)}
	/>
	{#if pageWidthPossible}
		<!-- The display also acts as the toggle – adding another button would overflow the bar. -->
		<button
			type="button"
			aria-pressed={wide}
			aria-label={t('review.zoom.button', {
				value: wide ? t('review.zoom.width') : t('review.zoom.value', { percent: zoom })
			})}
			title={t('review.zoom.pageWidth')}
			class="h-9.5 min-w-10.5 cursor-pointer rounded-[11px] px-1.5 text-center text-meta text-bar-muted tabular-nums hover:bg-bar-raised hover:text-bar-ink aria-pressed:bg-bar-raised aria-pressed:text-bar-ink"
			onclick={() => (pageWidth = !pageWidth)}
			>{wide ? t('review.zoom.width') : t('review.zoom.value', { percent: zoom })}</button
		>
	{:else}
		<span class="min-w-10.5 text-center text-meta text-bar-muted tabular-nums" aria-live="polite"
			>{t('review.zoom.value', { percent: zoom })}</span
		>
	{/if}
	<IconButton
		variant="bar"
		label={t('review.zoom.larger')}
		icon="larger"
		disabled={!wide && zoom === ZOOM_LEVELS[ZOOM_LEVELS.length - 1]}
		onclick={() => level(1)}
	/>
</div>
