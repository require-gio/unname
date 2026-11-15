<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import { diagnosticsText } from '#lib/settings/dialog.js';
	import { writeClipboard } from '#lib/sidecar/inputs.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { technicalInfo, ocr } from '#lib/sidecar/labels.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { change } from './change.js';

	/**
	 * Settings › Technical: runtime info for debugging and support.
	 * "Copy diagnostic report" includes only operational data, never document contents
	 * (spec §10). Developer mode shows process, device and versions — it does not affect processing.
	 */
	const gate = sidecarGate;
	const health = $derived(gate.health);
	const ready = $derived(gate.snapshot.status.state === 'ready');
	const developer = $derived(settings.values?.technical?.developer_mode ?? false);

	const detection = $derived.by(() => {
		const engine = health?.nlp_engine;
		if (!engine) return t('settings.technical.unknown');
		const name = engine.active ?? engine.selected ?? engine.preferred;
		return name === 'gliner'
			? t('settings.technical.gliner')
			: t('settings.technical.spacy', { name });
	});

	async function copy(): Promise<void> {
		try {
			await gate.loadDiagnostics().catch(() => undefined);
			const text = diagnosticsText({
				status: technicalInfo(gate.snapshot.status, t),
				restarts: gate.snapshot.restarts,
				version: health?.version,
				log: health?.protocol,
				python: health?.python,
				platform: health?.platform
					? `${health.platform.system} ${health.platform.machine}`
					: undefined,
				detection: health?.nlp_engine ? (health.nlp_engine.active ?? null) : undefined,
				device: health?.device?.active,
				pakete: health?.components,
				entries: gate.diagnostics.entries
			});
			await writeClipboard(text, 'plain');
			toast(t('settings.technical.copied'));
		} catch {
			toast.error(t('settings.error.general'));
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-6">
	<dl class="flex flex-col overflow-hidden rounded-lg border border-line">
		{#each [{ title: t('settings.technical.detection'), value: detection }, { title: t('settings.technical.sentences'), value: t('settings.technical.sentences.value') }, { title: t('settings.technical.ocr'), value: ocr(health?.ocr, t) }, { title: t('settings.technical.local'), value: ready ? t('settings.technical.local.ready') : t('settings.technical.local.not') }, { title: t('settings.technical.basis'), value: t( 'settings.technical.basis.value', { version: health?.version ?? '–' } ) }] as line (line.title)}
			<div
				class="grid grid-cols-[minmax(0,220px)_1fr] gap-4 border-b border-line-soft px-5 py-3 last:border-b-0"
			>
				<dt class="text-muted">{line.title}</dt>
				<dd class="font-medium text-ink">{line.value}</dd>
			</div>
		{/each}
	</dl>
	<div class="flex flex-wrap gap-2">
		<Button icon="copy" onclick={() => void copy()}>{t('settings.technical.diagnostics')}</Button>
		<Button
			variant="ghost"
			aria-pressed={developer}
			onclick={() => void change({ technical: { developer_mode: !developer } })}
		>
			{t('settings.technical.developer')}
		</Button>
	</div>
	{#if developer}
		<section
			aria-label={t('settings.technical.developer')}
			class="flex flex-col gap-3 rounded-lg border border-line bg-surface-3 px-5 py-4"
		>
			<dl class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-label" data-tabular>
				<dt class="text-muted">{t('settings.technical.process')}</dt>
				<dd class="font-mono break-all">{technicalInfo(gate.snapshot.status, t)}</dd>
				<dt class="text-muted">{t('settings.technical.restarts')}</dt>
				<dd class="font-mono">{gate.snapshot.restarts}</dd>
				{#if health?.platform && health.device && health.workers}
					<dt class="text-muted">{t('settings.technical.service')}</dt>
					<dd class="font-mono">{health.service} {health.version} · Protokoll {health.protocol}</dd>
					<dt class="text-muted">Python</dt>
					<dd class="font-mono">
						{health.python} · {health.platform.system}
						{health.platform.machine}
					</dd>
					<dt class="text-muted">Presidio</dt>
					<dd class="font-mono">{health.components['presidio-analyzer'] ?? '—'}</dd>
					<dt class="text-muted">{t('settings.technical.device')}</dt>
					<dd class="font-mono">
						{health.device.active} ({health.device.requested}, {health.device.probe})
					</dd>
					<dt class="text-muted">{t('settings.technical.worker')}</dt>
					<dd class="font-mono">{health.workers.cpu_workers}</dd>
				{/if}
			</dl>
			<Button size="sm" class="self-start" onclick={() => void gate.restart()}>
				{t('gate.restart')}
			</Button>
		</section>
	{/if}
</div>
