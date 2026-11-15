<script lang="ts">
	import { cn } from '#lib/utils.js';
	import Dialog from '#lib/ui/Dialog.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import { SECTIONS, type Section } from '#lib/settings/dialog.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names } from '#lib/state/names.svelte.js';
	import GeneralSection from './GeneralSection.svelte';
	import MaskingSection from './MaskingSection.svelte';
	import ModelsSection from './ModelsSection.svelte';
	import NamesSection from './NamesSection.svelte';
	import LanguagesSection from './LanguagesSection.svelte';
	import TableSection from './TableSection.svelte';
	import TechnicalSection from './TechnicalSection.svelte';
	import HistorySection from './HistorySection.svelte';
	import SecondOpinionSection from './SecondOpinionSection.svelte';

	/**
	 * Settings dialog (board "settings"): navigation with nine sections on the left,
	 * selected section on the right. It stays open while `page.state.einstellungen`
	 * names a section (shallow routing) — "back" and Escape close it.
	 *
	 * Changes apply immediately; there is no save button.
	 */
	const section = $derived(settings.section);
	/** Keep the component mounted when closing so the hide animation runs correctly. */
	let shown = $state<Section>('general');
	$effect(() => {
		if (section) shown = section;
	});

	// The notice dot on “Namen & Begriffe” needs the current state of the lists.
	$effect(() => {
		const connection = sidecarGate.connection;
		if (!section || !connection) return;
		void names.loadListen(connection).catch(() => undefined);
	});
</script>

<Dialog
	bind:open={() => section !== null, (open) => !open && settings.close()}
	title={t('settingsDialog.title')}
	description={t(`settings.${shown}.description`)}
	width="xl"
	withoutFrame
	class="h-[min(880px,calc(100vh-80px))]"
>
	<div class="flex min-h-0 flex-1">
		<nav
			aria-label={t('settingsDialog.title')}
			class="flex w-60 shrink-0 flex-col gap-0.5 border-r border-line bg-side px-3 pt-6 pb-5"
		>
			<p aria-hidden="true" class="px-3 pb-4 font-serif text-heading text-ink">
				{t('settingsDialog.title')}
			</p>
			{#each SECTIONS as entry (entry)}
				<button
					type="button"
					aria-current={entry === shown ? 'page' : undefined}
					onclick={() => settings.switch(entry)}
					class={cn(
						'flex items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-ui transition-colors',
						entry === shown
							? 'bg-surface font-semibold text-ink shadow-raised'
							: 'font-medium text-ink-2 hover:bg-surface-2'
					)}
				>
					<span>{t(`settings.${entry}`)}</span>
					{#if entry === 'names' && names.notice}
						<span
							role="img"
							aria-label={t('settings.names.notice')}
							class="size-2 shrink-0 rounded-full bg-amber-strong"
						></span>
					{/if}
				</button>
			{/each}
			<span class="flex-1"></span>
			<p class="px-3 text-caption text-subtle">{t('settingsDialog.live')}</p>
		</nav>
		<div class="flex min-w-0 flex-1 flex-col">
			<div class="flex items-start justify-between gap-4 border-b border-line-soft px-8 pt-7 pb-5">
				<div class="flex min-w-0 flex-col gap-1">
					<h2 class="font-serif text-display leading-tight font-normal text-ink">
						{t(`settings.${shown}`)}
					</h2>
					<p class="text-pretty text-muted">{t(`settings.${shown}.description`)}</p>
				</div>
				<IconButton
					label={t('common.close')}
					icon="close"
					variant="quiet"
					onclick={() => settings.close()}
				/>
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto px-8 pt-6 pb-10">
				{#if shown === 'general'}<GeneralSection />
				{:else if shown === 'masking'}<MaskingSection />
				{:else if shown === 'languages'}<LanguagesSection />
				{:else if shown === 'secondOpinion'}<SecondOpinionSection />
				{:else if shown === 'names'}<NamesSection />
				{:else if shown === 'history'}<HistorySection />
				{:else if shown === 'table'}<TableSection />
				{:else if shown === 'models'}<ModelsSection />
				{:else}<TechnicalSection />{/if}
			</div>
		</div>
	</div>
</Dialog>
