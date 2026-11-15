<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Badge from '#lib/ui/Badge.svelte';
	import Button from '#lib/ui/Button.svelte';
	import OptionCards from '#lib/ui/OptionCards.svelte';
	import Section from '#lib/ui/Section.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import { packageState } from '#lib/settings/languages.js';
	import { SECOND_OPINION_PRESET, type Policy } from '#lib/settings/second-opinion.js';
	import { errorKey } from '#lib/models/setup.js';
	import { fetchModels } from '#lib/sidecar/api-v2.js';
	import type { ModelsResponse, SecondOpinionSettings } from '#lib/sidecar/api.gen.js';
	import { istModelError } from '#lib/sidecar/models.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { models } from '#lib/state/models.svelte.js';
	import { change } from './change.js';

	/**
	 * Settings › Second opinion (AI): toggle, model state, and “How the reviewer works”.
	 * Applies to all detections from now on; open items are masked on save (“Mask when in doubt”).
	 */
	onMount(() => {
		const registered = models.listen();
		return () => void registered.then((abmelden) => abmelden());
	});

	const values = $derived({
		...SECOND_OPINION_PRESET,
		...(settings.values?.second_opinion ?? {})
	} as Required<SecondOpinionSettings>);
	const percent = $derived(Math.round(values.keep_threshold * 100));
	// From “AI decides nothing” to “AI decides most”.
	const politiken = $derived(
		(['suggest', 'protect', 'protect_and_keep'] as const).map((value) => ({
			value,
			title: t(`settings.secondOpinion.policy.${value}`),
			text: t(`settings.secondOpinion.policy.${value}.text`, { percent })
		}))
	);

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
	const part = $derived(models.overview?.components.find((item) => item.id === 'classifier'));
	const model = $derived(
		packageState(
			part,
			existing?.components.find((item) => item.id === 'classifier')
		)
	);
	const onlyFile = $derived(models.overview?.choice.source === 'file');

	function changeSecondOpinion(fresh: SecondOpinionSettings): void {
		void change({ second_opinion: fresh });
	}

	async function load(): Promise<void> {
		try {
			await models.herunterladen(['classifier']);
		} catch (error) {
			toast.error(t(errorKey(istModelError(error) ? error.code : undefined)));
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-8">
	<div class="flex flex-col gap-2">
		<SettingRow
			title={t('settings.secondOpinion.switch')}
			description={t('settings.secondOpinion.text')}
		>
			{#snippet children({ titleId })}
				<Switch
					labelledby={titleId}
					checked={values.enabled}
					oncheckedchange={(on) => changeSecondOpinion({ enabled: on })}
				/>
			{/snippet}
		</SettingRow>
		<p class="text-meta text-subtle">{t('settings.secondOpinion.scope')}</p>
	</div>

	<div
		class="flex items-center gap-4 rounded-lg border border-line px-4 py-3.5"
		data-testid="second-opinion-model"
	>
		<span class="flex min-w-0 flex-1 flex-col">
			<span class="font-semibold">{t('settings.secondOpinion.model')}</span>
			<span class="text-label text-muted">{t('settings.secondOpinion.model.text')}</span>
			{#if model.kind === 'missing'}
				<span class="text-meta text-amber-ink">{t('settings.secondOpinion.missing')}</span>
				{#if onlyFile}
					<span class="text-meta text-muted">{t('settings.languages.onlyFile')}</span>
				{/if}
			{/if}
			{#if part?.error && model.kind === 'missing'}
				<span role="alert" class="text-meta text-red-ink">
					{t(errorKey(part.error.code))}
				</span>
			{/if}
		</span>
		<span class="flex min-w-28 justify-end">
			{#if model.kind === 'running'}
				<Badge tone="tint">{t('settings.languages.loads', { percent: model.percent })}</Badge>
			{:else if model.kind === 'installed'}
				<Badge tone="green" icon="haken">{t('settings.languages.installed')}</Badge>
			{:else if model.kind === 'bundled'}
				<Badge tone={model.source === 'bundle' ? 'green' : 'neutral'}>
					{t(model.source === 'bundle' ? 'models.source.bundle' : 'models.source.local')}
				</Badge>
			{:else if part}
				<Button variant="link" size="sm" disabled={models.running || onlyFile} onclick={load}>
					{t(model.started ? 'settings.languages.continue' : 'settings.secondOpinion.load')}
				</Button>
			{/if}
		</span>
	</div>

	<Section title={t('settings.secondOpinion.policy')}>
		<OptionCards
			layout="list"
			label={t('settings.secondOpinion.policy')}
			options={politiken}
			value={values.policy as Policy}
			onwechsel={(policy) => changeSecondOpinion({ policy })}
		/>
	</Section>

	<Section title={t('settings.secondOpinion.reviewerTitle')}>
		<ol class="flex list-decimal flex-col gap-2 pl-5 text-pretty text-muted">
			<li>{t('settings.secondOpinion.reviewer.1')}</li>
			<li>{t('settings.secondOpinion.reviewer.2')}</li>
			<li>{t('settings.secondOpinion.reviewer.3')}</li>
			<li>{t('settings.secondOpinion.reviewer.4')}</li>
		</ol>
	</Section>
</div>
