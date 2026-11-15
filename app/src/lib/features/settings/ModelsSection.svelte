<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { cn } from '#lib/utils.js';
	import Badge from '#lib/ui/Badge.svelte';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Progress from '#lib/ui/Progress.svelte';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import Checksums from '#lib/features/models/Checksums.svelte';
	import SourceChoice from '#lib/features/models/SourceChoice.svelte';
	import { size } from '#lib/settings/dialog.js';
	import { componentText, errorKey, running, needed, percent } from '#lib/models/setup.js';
	import { fetchModels } from '#lib/sidecar/api-v2.js';
	import type { ModelsResponse } from '#lib/sidecar/api.gen.js';
	import { istModelError, type ModelPart } from '#lib/sidecar/models.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { models } from '#lib/state/models.svelte.js';

	/**
	 * Settings › Models & Storage: inventory, “Review now”,
	 * remove, source, package file and the list of checksums. Load,
	 * review and remove are handled by the harness; what the sidecar actually
	 * finds (including bundled), comes from `GET /models`.
	 */
	onMount(() => {
		const registered = models.listen();
		return () => void registered.then((abmelden) => abmelden());
	});

	let existing = $state<ModelsResponse | null>(null);
	let checksums = $state(false);
	let reviewRunning = $state(false);
	let removeQuestion = $state<ModelPart | null>(null);

	/** Installed components – stable across progress events. */
	const installedState = $derived(
		models.overview?.components
			.map((part) => `${part.id}:${part.state === 'installed'}`)
			.join(',') ?? ''
	);

	// Re-fetch when the shell's state changes or the sidecar restarts.
	$effect(() => {
		const connection = sidecarGate.connection;
		void installedState;
		if (!connection) return;
		fetchModels(connection)
			.then((answer) => (existing = answer))
			.catch(() => (existing = null));
	});

	const overview = $derived(models.overview);
	const parts = $derived(overview?.components ?? []);
	const locale = $derived(i18n.language === 'en' ? 'en-GB' : 'de-DE');
	const date = (iso: string) =>
		new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
			new Date(iso)
		);

	function sourceInSidecar(part: ModelPart) {
		return existing?.components.find((item) => item.id === part.id);
	}

	/**
	 * Bundled or available in the development environment. The shell knows
	 * its own source – an older sidecar version does not count.
	 */
	function bundled(part: ModelPart) {
		const sidecar = sourceInSidecar(part);
		return sidecar?.state === 'ready' && sidecar.source !== 'download' ? sidecar : undefined;
	}

	/** Available – installed, bundled, or in the development environment. */
	function isPresent(part: ModelPart): boolean {
		return part.state === 'installed' || bundled(part) !== undefined;
	}

	/**
	 * System OCR: on macOS Apple Vision handles OCR. The shell does not provide
	 * an OCR component; the entry comes from the sidecar bundle and requires
	 * no load or removal.
	 */
	const systemOcr = $derived(
		existing?.components.find((item) => item.id === 'ocr' && item.source === 'system') &&
			!parts.some((part) => part.id === 'ocr')
	);
	const ocrIsPrepared = $derived(sidecarGate.health?.ocr?.state === 'warming');

	const installedOnes = $derived(parts.filter(isPresent));
	const total = $derived(
		installedOnes.reduce(
			(sum, part) =>
				sum + (part.state === 'installed' ? part.installed_size : (bundled(part)?.size_bytes ?? 0)),
			0
		)
	);
	const loaded = $derived(parts.filter((part) => part.state === 'installed'));
	const lastReviewed = $derived(
		loaded
			.map((part) => part.checked_at)
			.filter((value): value is string => value !== null)
			.sort()[0] ?? null
	);

	function showError(error: unknown): void {
		toast.error(t(errorKey(istModelError(error) ? error.code : undefined)));
	}

	async function load(part: ModelPart): Promise<void> {
		try {
			await models.herunterladen([part.id]);
		} catch (error) {
			showError(error);
		}
	}

	async function nowReview(): Promise<void> {
		reviewRunning = true;
		try {
			const report = await models.review();
			if (report.damaged.length === 0) toast(t('models.review.ok'));
			else toast.error(t('models.review.deviation', { n: report.damaged.length }));
		} catch (error) {
			showError(error);
		} finally {
			reviewRunning = false;
		}
	}

	async function remove(part: ModelPart): Promise<void> {
		try {
			await models.remove(part.id);
			toast(t('models.removed', { name: t(componentText(part).name) }));
		} catch (error) {
			showError(error);
		}
	}

	async function fromFile(): Promise<void> {
		try {
			if (
				await models.bundle({
					title: t('models.package.dialog'),
					filter: t('models.package.filter')
				})
			)
				toast(t('models.package.done'));
		} catch (error) {
			showError(error);
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-6">
	{#if !overview}
		{#if models.notAvailable}
			<p role="status" class="text-subtle">{t('models.notAvailable')}</p>
		{:else}
			<Skeleton class="h-40" />
		{/if}
	{:else}
		<div class="flex flex-wrap items-center gap-4 rounded-lg border border-line px-5 py-4">
			<span class="font-serif text-display leading-none">{size(total, locale)}</span>
			<span class="flex flex-[1_1_220px] flex-col">
				<span class="font-semibold">
					{t('settings.models.total', { n: installedOnes.length })}
				</span>
				<span class="text-label text-muted">
					{#if loaded.length === 0}
						{t('models.existing.withoutDownload')}
					{:else if lastReviewed}
						{t('models.existing.reviewed', { date: date(lastReviewed) })}
					{:else}
						{t('models.existing.unreviewed')}
					{/if}
				</span>
			</span>
			<Button
				size="sm"
				busy={reviewRunning}
				disabled={loaded.length === 0 || models.running}
				onclick={nowReview}
			>
				{t('models.review')}
			</Button>
		</div>

		<ul class="flex flex-col overflow-hidden rounded-lg border border-line">
			{#each parts as part (part.id)}
				{@const text = componentText(part)}
				{@const sidecar = bundled(part)}
				<li class="flex items-center gap-3.5 border-b border-line-soft px-4 py-3 last:border-b-0">
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="font-semibold">{t(text.name)}</span>
						<span class="text-meta text-muted">{t(text.text)}</span>
						{#if part.state === 'downloading'}
							<Progress
								value={percent(part)}
								label={t('models.line.percent', {
									percent: percent(part),
									size: size(part.download_size, locale)
								})}
								class="mt-1.5"
							/>
						{/if}
						{#if part.error && !isPresent(part)}
							<span role="alert" class="text-meta text-red-ink">
								{t(errorKey(part.error.code))}
							</span>
						{/if}
					</span>
					<span class="text-label text-muted tabular-nums">
						{#if part.state === 'downloading'}
							{t('models.line.percent', {
								percent: percent(part),
								size: size(part.download_size, locale)
							})}
						{:else}
							{size(
								part.state === 'installed'
									? part.installed_size
									: (sidecar?.size_bytes ?? part.download_size),
								locale
							)}
						{/if}
					</span>
					<span class="flex min-w-24 justify-end">
						{#if running(part)}
							<Badge tone="tint">{t(`models.state.${part.state}`)}</Badge>
						{:else if part.state === 'installed' && part.kind === 'ner'}
							<Badge>{t('models.required')}</Badge>
						{:else if part.state === 'installed'}
							<Button
								variant="link"
								size="sm"
								disabled={models.running}
								onclick={() => (removeQuestion = part)}
							>
								{t('models.remove')}
							</Button>
						{:else if sidecar}
							<Badge tone={sidecar.source === 'bundle' ? 'green' : 'neutral'}>
								{t(sidecar.source === 'bundle' ? 'models.source.bundle' : 'models.source.local')}
							</Badge>
						{:else}
							<Button
								variant="link"
								size="sm"
								disabled={models.running || overview.choice.source === 'file'}
								onclick={() => load(part)}
							>
								{part.state === 'partial' ? t('models.continue') : t('models.load')}
							</Button>
						{/if}
					</span>
				</li>
			{/each}
			{#if systemOcr}
				<li class="flex items-center gap-3.5 border-b border-line-soft px-4 py-3 last:border-b-0">
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="font-semibold">{t('models.ocr')}</span>
						<span class="text-meta text-muted">{t('models.ocr.text.system')}</span>
					</span>
					<span class="flex min-w-24 justify-end">
						{#if ocrIsPrepared}
							<Badge tone="tint">{t('models.ocr.preparation')}</Badge>
						{:else}
							<Badge tone="green">{t('models.source.system')}</Badge>
						{/if}
					</span>
				</li>
			{/if}
		</ul>

		<div class="flex flex-col gap-2.5">
			<SourceChoice id="models-source" label={t('models.source.lang')} withFile />
			<p class="text-ui text-pretty text-muted">{t('models.network')}</p>
		</div>
		<div class="flex flex-wrap gap-2">
			<Button size="sm" icon="folder" disabled={models.running} onclick={fromFile}>
				{t('models.package')}
			</Button>
			<Button
				variant="link"
				size="sm"
				aria-expanded={checksums}
				onclick={() => (checksums = !checksums)}
			>
				{checksums ? t('models.checksums.from') : t('models.checksums.on')}
			</Button>
		</div>
		{#if checksums}
			<div class={cn('rounded-lg border border-line bg-surface-3 px-4 py-3.5')}>
				<Checksums components={parts} />
			</div>
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={() => removeQuestion !== null, (open) => !open && (removeQuestion = null)}
	title={t('models.remove.title', {
		name: removeQuestion ? t(componentText(removeQuestion).name) : ''
	})}
	description={t('models.remove.text')}
	sequences={removeQuestion && needed(removeQuestion, parts)
		? [t('models.remove.sequence.setup'), t('models.remove.sequence.loadNew')]
		: [t('models.remove.sequence.loadNew')]}
	confirm={t('models.remove.ja')}
	onconfirm={() => {
		const part = removeQuestion;
		removeQuestion = null;
		if (part) void remove(part);
	}}
/>
