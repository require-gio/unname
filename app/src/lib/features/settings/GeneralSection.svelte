<script lang="ts">
	import { isTauri } from '@tauri-apps/api/core';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import OptionCards from '#lib/ui/OptionCards.svelte';
	import Section from '#lib/ui/Section.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import { windowSettings, setWindowSettings, type WindowSettings } from '#lib/sidecar/system.js';
	import type { Locale } from '#lib/i18n/translate.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { change } from './change.js';

	/**
	 * Settings › General: app language and appearance.
	 * Changing the UI language does not affect how documents are reviewed.
	 *
	 * “Hide window in screen shares” is stored by the shell,
	 * not the sidecar; it applies before the sidecar starts.
	 */
	type Appearance = 'light' | 'dark' | 'system';

	const language = $derived(i18n.language);
	const appearance = $derived<Appearance>(settings.values?.general?.theme ?? 'system');

	const languages = [
		{ value: 'de' as const, title: 'Deutsch', text: 'German', shortCode: 'DE' },
		{ value: 'en' as const, title: 'English', text: 'Englisch', shortCode: 'EN' }
	];
	let viewport = $state<WindowSettings | null>(null);
	onMount(() => {
		if (!isTauri()) return;
		void windowSettings()
			.then((values) => (viewport = values))
			.catch(() => {
				// If the request fails, keep the switch hidden.
			});
	});

	async function hide(on: boolean): Promise<void> {
		const before = viewport;
		if (!before) return;
		viewport = { ...before, hide_from_capture: on };
		try {
			viewport = await setWindowSettings(viewport);
		} catch {
			viewport = before;
			toast.error(t('settings.general.release.error'));
		}
	}

	const appearances = $derived([
		{ value: 'light' as const, label: t('settingsDialog.appearance.light') },
		{ value: 'dark' as const, label: t('settingsDialog.appearance.dark') },
		{ value: 'system' as const, label: t('settingsDialog.appearance.system') }
	]);
</script>

<div class="flex max-w-170 flex-col gap-9">
	<Section
		title={t('settingsDialog.appLanguage')}
		description={t('settings.general.language.text')}
	>
		<OptionCards
			layout="language"
			label={t('settingsDialog.appLanguage')}
			options={languages}
			value={language}
			onwechsel={(fresh: Locale) => void change({ general: { language: fresh } })}
		/>
		<p class="flex items-center gap-2 text-label text-subtle">
			<Icon name="new" size={14} />{t('settings.general.language.more')}
		</p>
		<div class="flex items-start gap-3 rounded-lg border border-line bg-surface-3 px-4 py-3.5">
			<Icon name="info" size={17} class="mt-0.5 shrink-0 text-muted" />
			<p class="text-label text-pretty text-ink-2">
				{t('settings.general.language.notice')}
				<Button variant="link" size="sm" onclick={() => settings.switch('languages')}>
					{t('settings.general.toLanguages')}
				</Button>
			</p>
		</div>
	</Section>
	<Section title={t('settingsDialog.appearance')}>
		<Segmented
			label={t('settingsDialog.appearance')}
			options={appearances}
			value={appearance}
			class="self-start"
			onwechsel={(fresh: Appearance) => void change({ general: { theme: fresh } })}
		/>
	</Section>
	{#if viewport}
		<Section title={t('settings.general.release')}>
			<SettingRow
				title={t('settings.general.release.title')}
				description={t('settings.general.release.text')}
			>
				{#snippet children({ titleId })}
					<Switch
						labelledby={titleId}
						checked={viewport?.hide_from_capture ?? false}
						oncheckedchange={(on) => void hide(on)}
					/>
				{/snippet}
			</SettingRow>
		</Section>
	{/if}
</div>
