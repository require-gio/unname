<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Badge from '#lib/ui/Badge.svelte';
	import Button from '#lib/ui/Button.svelte';
	import Chip from '#lib/ui/Chip.svelte';
	import Section from '#lib/ui/Section.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import {
		DOCUMENT_LANGUAGES,
		COUNTRIES,
		COUNTRIES_SEQUENCES,
		LANGUAGE_PRESET,
		toggleCountry,
		packageText,
		packageState,
		toggleLanguage,
		type DocumentLanguage,
		type Country
	} from '#lib/settings/languages.js';
	import { errorKey } from '#lib/models/setup.js';
	import { fetchModels } from '#lib/sidecar/api-v2.js';
	import type { DocumentLanguageSettings, ModelsResponse } from '#lib/sidecar/api.gen.js';
	import { istModelError } from '#lib/sidecar/models.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { models } from '#lib/state/models.svelte.js';
	import { change } from './change.js';

	/**
	 * Document language settings: per-paragraph detection, which languages to check
	 * (load language packs), countries for numbers and IDs, and placeholder language.
	 * Applies to all future detections; loading and checking are handled by the shell.
	 */
	onMount(() => {
		const registered = models.listen();
		return () => void registered.then((abmelden) => abmelden());
	});

	const values = $derived({
		...LANGUAGE_PRESET,
		...(settings.values?.document_languages ?? {})
	} as Required<DocumentLanguageSettings>);
	const languages = $derived(values.languages as readonly DocumentLanguage[]);
	const countries = $derived(values.countries as readonly Country[]);

	let existing = $state<ModelsResponse | null>(null);
	const installedState = $derived(
		models.overview?.components
			.map((part) => `${part.id}:${part.state === 'installed'}`)
			.join(',') ?? ''
	);
	$effect(() => {
		const connection = sidecarGate.connection;
		void installedState;
		if (!connection) return;
		fetchModels(connection)
			.then((answer) => (existing = answer))
			.catch(() => (existing = null));
	});

	function status(language: DocumentLanguage) {
		return packageState(
			models.overview?.components.find((part) => part.id === language),
			existing?.components.find((part) => part.id === language)
		);
	}

	const onlyFile = $derived(models.overview?.choice.source === 'file');

	function changeLanguages(part: DocumentLanguageSettings): void {
		void change({ document_languages: part });
	}

	function language(code: DocumentLanguage): void {
		const fresh = toggleLanguage(languages, code);
		if (fresh) changeLanguages({ languages: fresh });
		else toast(t('settings.languages.last'));
	}

	function country(code: Country): void {
		const fresh = toggleCountry(countries, code);
		if (fresh) changeLanguages({ countries: fresh });
		else toast(t('settings.languages.countries.last'));
	}

	async function load(code: DocumentLanguage): Promise<void> {
		try {
			await models.herunterladen([code]);
		} catch (error) {
			toast.error(t(errorKey(istModelError(error) ? error.code : undefined)));
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-8">
	<SettingRow
		title={t('settings.languages.detect')}
		description={t('settings.languages.detect.text')}
	>
		{#snippet children({ titleId })}
			<Switch
				labelledby={titleId}
				checked={values.per_paragraph}
				oncheckedchange={(on) => changeLanguages({ per_paragraph: on })}
			/>
		{/snippet}
	</SettingRow>

	<Section
		title={t('settings.languages.packages')}
		description={t('settings.languages.packages.text')}
	>
		<ul class="flex flex-col overflow-hidden rounded-lg border border-line">
			{#each DOCUMENT_LANGUAGES as code (code)}
				{@const text = packageText(code)}
				{@const bundle = status(code)}
				{@const on = languages.includes(code)}
				{@const part = models.overview?.components.find((item) => item.id === code)}
				<li class="flex items-center gap-4 border-b border-line-soft px-4 py-3.5 last:border-b-0">
					<span
						aria-hidden="true"
						class="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-chip font-mono text-caption font-medium text-muted uppercase"
						>{code}</span
					>
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="font-semibold">{t(text.name)}</span>
						<span class="text-label text-muted">{t(text.text)}</span>
						{#if on && bundle.kind === 'missing'}
							<span class="text-meta text-amber-ink">{t('settings.languages.missing')}</span>
						{/if}
						{#if part?.error && bundle.kind === 'missing'}
							<span role="alert" class="text-meta text-red-ink">
								{t(errorKey(part.error.code))}
							</span>
						{/if}
					</span>
					<span class="flex min-w-28 justify-end">
						{#if bundle.kind === 'running'}
							<Badge tone="tint">{t('settings.languages.loads', { percent: bundle.percent })}</Badge
							>
						{:else if bundle.kind === 'installed'}
							<Badge tone="green" icon="haken">{t('settings.languages.installed')}</Badge>
						{:else if bundle.kind === 'bundled'}
							<Badge tone={bundle.source === 'bundle' ? 'green' : 'neutral'}>
								{t(bundle.source === 'bundle' ? 'models.source.bundle' : 'models.source.local')}
							</Badge>
						{:else if models.overview}
							<Button
								variant="link"
								size="sm"
								disabled={models.running || onlyFile}
								onclick={() => load(code)}
							>
								{bundle.started ? t('settings.languages.continue') : t('settings.languages.load')}
							</Button>
						{:else}
							<Badge>{t('models.state.missing')}</Badge>
						{/if}
					</span>
					<!-- Keep at least one language enabled because the sidecar requires a minimum of one. -->
					<Switch
						checked={on}
						disabled={on && languages.length === 1}
						label={t('settings.languages.review', { language: t(text.name) })}
						oncheckedchange={() => language(code)}
					/>
				</li>
			{/each}
		</ul>
		{#if onlyFile && DOCUMENT_LANGUAGES.some((code) => status(code).kind === 'missing')}
			<p class="text-label text-pretty text-subtle">{t('settings.languages.onlyFile')}</p>
		{/if}
	</Section>

	<Section
		title={t('settings.languages.countries')}
		description={t('settings.languages.countries.text')}
	>
		<ul class="flex flex-wrap gap-2" aria-label={t('settings.languages.countries')}>
			{#each COUNTRIES as code (code)}
				<li>
					<Chip on={countries.includes(code)} onclick={() => country(code)}>
						{t(`settings.country.${code}`)}
					</Chip>
				</li>
			{/each}
			{#each COUNTRIES_SEQUENCES as code (code)}
				<li>
					<Badge>
						{t(`settings.country.${code}`)}<span class="font-normal"
							>&nbsp;· {t('settings.follows')}</span
						>
					</Badge>
				</li>
			{/each}
		</ul>
	</Section>

	<SettingRow
		title={t('settings.languages.placeholder')}
		description={t('settings.languages.placeholder.text')}
	>
		{#snippet children({ titleId })}
			<Switch
				labelledby={titleId}
				checked={values.placeholders_in_document_language}
				oncheckedchange={(on) => changeLanguages({ placeholders_in_document_language: on })}
			/>
		{/snippet}
	</SettingRow>

	<p class="text-label text-subtle">{t('settings.languages.scope')}</p>
</div>
