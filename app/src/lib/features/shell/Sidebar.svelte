<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { cn } from '#lib/utils.js';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Logo from '#lib/ui/Logo.svelte';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import { returnPath } from '#lib/saved/saved.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { ui } from '#lib/state/ui.svelte.js';
	import { history } from '#lib/state/history.svelte.js';
	import {
		historyCounts,
		historyRow,
		parseScope,
		recentEntries,
		type HistoryScope
	} from '#lib/history/history.js';
	import { historyHref } from '#lib/history/links.js';
	import type { IconName } from '#lib/ui/icons.js';
	import RecentEntries from './RecentEntries.svelte';

	/**
	 * Sidebar UI: logo, “+ New”, navigation tabs “Open”, “Completed”, “All entries”
	 * with counters, the three most recent entries, and a settings button at the bottom.
	 * The sidebar height follows the history length; when collapsed it stays a 60 px
	 * strip showing the brand, expand control, the three history targets and settings.
	 *
	 * The settings button opens a dialog overlaying the current view.
	 */

	const onStart = $derived(page.route.id === '/(app)');
	const activeEntry = $derived(page.params.id);

	const onHistory = $derived(page.route.id === '/(app)/history');
	const currentSection = $derived(onHistory ? parseScope(page.url.searchParams.get('view')) : null);
	const counter = $derived(historyCounts(history.entries));
	const last = $derived(
		recentEntries(history.entries).map((entry) => historyRow(entry, new Date(), i18n.language, t))
	);
	const lastState = $derived(
		!history.loaded && !history.error
			? 'loads'
			: history.error && history.entries.length === 0
				? 'error'
				: 'ready'
	);

	const TARGETS: readonly { scope: HistoryScope; icon: IconName }[] = [
		{ scope: 'open', icon: 'open' },
		{ scope: 'completed', icon: 'abgeschlossen' },
		{ scope: 'all', icon: 'allEntries' }
	];

	function sectionName(scope: HistoryScope): string {
		return scope === 'open'
			? t('history.scope.open')
			: scope === 'completed'
				? t('history.scope.completed')
				: t('history.scope.all');
	}

	/** Show count only after history is loaded – avoid showing a premature zero. */
	function sectionLabel(scope: HistoryScope): string {
		return history.loaded
			? t('history.nav.count', { label: sectionName(scope), n: counter[scope] })
			: sectionName(scope);
	}

	const retention = $derived(settings.historyRetention);
	/** The note number follows the view markers. */
	// The note uses the mockup numbers: Start and
	// Restore 2, Review 4, Saved with Restore 3 (preceded by the card notes).
	const savedWithReturnPath = $derived(
		page.route.id === '/(app)/entry/[id]' &&
			entryViewModel.entry?.kind === 'documents' &&
			entryViewModel.entry.status === 'saved' &&
			returnPath(entryViewModel.entry) === 'restore'
	);
	const number = $derived(
		onStart || page.route.id === '/(app)/entry/[id]/restore'
			? 2
			: page.route.id === '/(app)/entry/[id]/review'
				? 4
				: savedWithReturnPath
					? 3
					: 1
	);
</script>

{#if ui.sidebarVisible}
	<aside
		aria-label={t('historyList.title')}
		class="flex w-64 shrink-0 flex-col gap-1 border-r border-line bg-side px-2.5 py-3"
	>
		<div class="flex items-center justify-between gap-2 pt-0.5 pr-0.5 pb-2.5 pl-2">
			<Logo variant="full" size={28} dekorativ={false} />
			<IconButton
				label={t('sidebar.collapse')}
				icon="sidebar"
				size="sm"
				onclick={() => ui.toggleSidebar()}
			/>
		</div>
		<a
			href={resolve('/(app)')}
			aria-current={onStart ? 'page' : undefined}
			class={cn(
				'flex h-10.5 items-center gap-2.5 rounded-md border bg-surface px-3 font-semibold text-ink no-underline transition-colors',
				onStart
					? 'border-[1.5px] border-accent halo-accent'
					: 'border-line hover:border-line-strong'
			)}
		>
			<Icon name="new" size={17} />{t('sidebar.new')}
		</a>
		<div class="mt-3 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
			<nav
				aria-label={t('history.nav.label')}
				class="flex flex-col gap-px"
				{@attach coachmark(
					number,
					retention === null ? 'help.history.never' : 'help.history.retention',
					{
						page: 'right',
						values: retention === null ? undefined : { days: retention }
					}
				)}
			>
				{#each TARGETS as target (target.scope)}
					{@const current = currentSection === target.scope}
					<a
						href={historyHref(target.scope)}
						aria-current={current ? 'page' : undefined}
						aria-label={sectionLabel(target.scope)}
						class={cn(
							'flex items-center gap-2.5 rounded-sm px-2.5 py-2 no-underline transition-colors',
							current
								? 'bg-surface font-semibold text-ink shadow-1'
								: 'font-medium text-ink-2 hover:bg-surface-2'
						)}
					>
						<Icon name={target.icon} size={17} strokeWidth={1.8} />
						<span class="min-w-0 flex-1 truncate">{sectionName(target.scope)}</span>
						{#if history.loaded}
							<span aria-hidden="true" class="text-caption text-subtle tabular-nums"
								>{counter[target.scope]}</span
							>
						{/if}
					</a>
				{/each}
			</nav>
			<RecentEntries rows={last} active={activeEntry} status={lastState} />
		</div>
		<div class="border-t border-line pt-2">
			<button
				type="button"
				aria-haspopup="dialog"
				onclick={() => settings.open()}
				class="flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2.25 text-left font-medium text-ink-2 transition-colors hover:bg-surface-2"
			>
				<Icon name="settings" size={17} strokeWidth={1.8} />{t('sidebar.settings')}
			</button>
		</div>
	</aside>
{:else}
	<aside
		aria-label={t('historyList.title')}
		class="flex w-15 shrink-0 flex-col items-center gap-2 overflow-y-auto border-r border-line bg-side py-3"
	>
		<Logo size={28} class="mb-1.5" />
		<IconButton label={t('sidebar.expand')} icon="sidebar" onclick={() => ui.toggleSidebar()} />
		<IconButton
			href={resolve('/(app)')}
			label={t('sidebar.new')}
			icon="new"
			variant="secondary"
			aria-current={onStart ? 'page' : undefined}
		/>
		<nav aria-label={t('history.nav.label')} class="mt-1.5 flex flex-col items-center gap-1">
			{#each TARGETS as target (target.scope)}
				<IconButton
					href={historyHref(target.scope)}
					label={sectionLabel(target.scope)}
					icon={target.icon}
					aria-current={currentSection === target.scope ? 'page' : undefined}
					class={currentSection === target.scope ? 'bg-surface text-ink shadow-1' : undefined}
				/>
			{/each}
		</nav>
		<span class="flex-1"></span>
		<IconButton
			label={t('sidebar.settings')}
			icon="settings"
			aria-haspopup="dialog"
			onclick={() => settings.open()}
		/>
	</aside>
{/if}
