<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { cn } from '#lib/utils.js';
	import Badge from '#lib/ui/Badge.svelte';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import Logo from '#lib/ui/Logo.svelte';
	import Progress from '#lib/ui/Progress.svelte';
	import Select from '#lib/ui/Select.svelte';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import { size } from '#lib/settings/dialog.js';
	import { isLocale, type Locale } from '#lib/i18n/translate.js';
	import {
		componentText,
		errorKey,
		progress,
		running,
		needed,
		neededThere,
		percent,
		restSize,
		startSelection,
		toggle,
		loadTo
	} from '#lib/models/setup.js';
	import { istModelError, type ModelPart } from '#lib/sidecar/models.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { models } from '#lib/state/models.svelte.js';
	import Checksums from './Checksums.svelte';
	import SourceChoice from './SourceChoice.svelte';

	/**
	 * Board "0 · Setup" : What should be on this device? The shell loads once, checks each file against the signed app manifest and installs only then. Once name detection and language are ready, the gate "Deposit" is released – "Start" follows it.
	 */
	onMount(() => {
		const registered = models.listen();
		return () => void registered.then((abmelden) => abmelden());
	});

	// The app language can be selected here.
	$effect(() => {
		const connection = sidecarGate.connection;
		if (connection) void settings.load(connection);
	});

	const overview = $derived(models.overview);
	const parts = $derived(overview?.components ?? []);
	const existing = $derived(sidecarGate.health?.models ?? {});
	const locale = $derived(i18n.language === 'en' ? 'en-GB' : 'de-DE');

	let selection = $state<Set<string> | null>(null);
	/** What the current or last load operation includes; `null`: no selection. */
	let loaded = $state<string[] | null>(null);

	$effect(() => {
		if (!overview) return;
		selection ??= startSelection(overview);
		// Already something running (view reopened), show progress.
		if (loaded === null && overview.busy) {
			const laufend = overview.components.filter(running).map((part) => part.id);
			if (laufend.length > 0) loaded = laufend;
		}
	});

	const ids = $derived(overview && selection ? loadTo(overview, selection) : []);
	const rest = $derived(overview ? restSize(overview, ids) : 0);
	const status = $derived(overview && loaded ? progress(overview, loaded) : { done: 0, total: 0 });
	const onlyFile = $derived(overview?.choice.source === 'file');
	const neededPresent = $derived(overview ? neededThere(overview, existing) : false);
	/** The gate decided: uploading is allowed. */
	const ready = $derived(sidecarGate.snapshot.setup_required === false && sidecarGate.mayUpload);
	const stillInBackground = $derived(parts.some(running));

	const languages = [
		{ value: 'de' as const, label: 'Deutsch' },
		{ value: 'en' as const, label: 'English' }
	];

	async function selectLanguage(fresh: Locale): Promise<void> {
		const connection = sidecarGate.connection;
		if (connection) await settings.change(connection, { general: { language: fresh } });
		else i18n.set(fresh);
	}

	function showError(error: unknown): void {
		toast.error(t(errorKey(istModelError(error) ? error.code : undefined)));
	}

	async function start(): Promise<void> {
		if (ids.length === 0) return;
		const extent = [...ids];
		try {
			await models.herunterladen(extent);
			loaded = extent;
		} catch (error) {
			showError(error);
		}
	}

	async function cancel(): Promise<void> {
		try {
			await models.cancel();
		} finally {
			loaded = null;
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

	function selected(part: ModelPart): boolean {
		return needed(part, parts) || (selection?.has(part.id) ?? false);
	}

	function right(part: ModelPart): { text: string; tone: string } {
		const sizeText = size(part.download_size, locale);
		if (loaded === null) {
			if (part.state === 'installed')
				return { text: t('models.line.installed'), tone: 'text-green-ink' };
			if (part.state === 'partial')
				return {
					text: t('models.line.started', { percent: percent(part), size: sizeText }),
					tone: 'text-muted'
				};
			return { text: sizeText, tone: 'text-muted' };
		}
		if (!loaded.includes(part.id) && part.state !== 'installed')
			return { text: t('models.line.skipped'), tone: 'text-faint' };
		switch (part.state) {
			case 'installed':
				return {
					text: t('models.line.reviewed', { size: sizeText }),
					tone: 'font-semibold text-green-ink'
				};
			case 'downloading':
				return {
					text: t('models.line.percent', { percent: percent(part), size: sizeText }),
					tone: 'font-semibold text-link'
				};
			case 'verifying':
			case 'installing':
				return { text: t('models.line.review'), tone: 'font-semibold text-link' };
			case 'failed':
				return { text: t('models.line.error'), tone: 'font-semibold text-red-ink' };
			default:
				return { text: t('models.line.waits', { size: sizeText }), tone: 'text-subtle' };
		}
	}
</script>

<div class="min-h-dvh bg-bg px-6 pt-9 pb-12 text-ink">
	<div class="mx-auto flex max-w-[940px] flex-col gap-6">
		<header class="flex flex-wrap items-center justify-between gap-4">
			<Logo variant="full" size={28} dekorativ={false} />
			<span class="flex items-center gap-2.5">
				<label for="setup-language" class="text-label text-muted">
					{t('settingsDialog.appLanguage')}
				</label>
				<Select
					id="setup-language"
					value={i18n.language}
					options={languages}
					onchange={(event) => {
						const fresh = event.currentTarget.value;
						if (isLocale(fresh)) void selectLanguage(fresh);
					}}
				/>
			</span>
		</header>

		<div class="flex max-w-190 flex-col gap-2.5">
			<span class="text-meta font-semibold tracking-[0.04em] text-link uppercase">
				{t('setup.eyebrow')}
			</span>
			<h1 class="font-serif text-display-lg font-normal tracking-[-0.015em] text-balance">
				{t('setup.title')}
			</h1>
			<p class="text-doc text-pretty text-muted">{t('setup.text')}</p>
		</div>

		<section
			aria-labelledby="setup-parts"
			class="flex flex-col overflow-hidden rounded-xl border border-line bg-surface"
		>
			<div class="flex items-baseline justify-between gap-3 border-b border-line-soft px-5 py-4">
				<h2 id="setup-parts" class="text-lead font-semibold">
					{loaded ? t('setup.list.load') : t('setup.list.selection')}
				</h2>
				<span class="text-label text-muted">
					{#if loaded}
						{t('setup.done', { done: status.done, total: status.total })}
					{:else if overview}
						{t('setup.total', { size: size(rest, locale) })}
					{/if}
				</span>
			</div>
			{#if !overview}
				<div class="p-5">
					{#if models.notAvailable}
						<p role="status" class="text-subtle">{t('models.notAvailable')}</p>
					{:else}
						<Skeleton class="h-40" />
					{/if}
				</div>
			{:else}
				<ul>
					{#each parts as part (part.id)}
						{@const text = componentText(part)}
						{@const on = selected(part)}
						{@const locked = needed(part, parts) || part.state === 'installed'}
						{@const target = right(part)}
						<li
							class="flex items-center gap-4 border-b border-line-soft px-5 py-3.5 last:border-b-0"
						>
							{#if loaded === null}
								<button
									type="button"
									role="checkbox"
									aria-checked={on || part.state === 'installed'}
									aria-label={t(text.name)}
									disabled={locked || models.running}
									onclick={() => selection && (selection = toggle(selection, part, parts))}
									class={cn(
										'flex size-6 shrink-0 items-center justify-center rounded-sm border-[1.5px] text-on-accent',
										locked
											? 'cursor-default border-faint bg-faint'
											: on
												? 'cursor-pointer border-accent bg-accent'
												: 'cursor-pointer border-line-strong bg-surface'
									)}
								>
									{#if on || part.state === 'installed'}<Icon name="haken" size={14} />{/if}
								</button>
							{:else}
								<span
									aria-hidden="true"
									class={cn(
										'flex size-6 shrink-0 items-center justify-center rounded-full text-label font-bold',
										part.state === 'installed'
											? 'bg-ink text-on-ink'
											: part.state === 'failed'
												? 'bg-red-1 text-red-ink'
												: running(part) && part.state !== 'queued'
													? 'bg-tint-2 text-link'
													: 'bg-chip text-subtle'
									)}
								>
									{#if part.state === 'installed'}
										<Icon name="haken" size={14} />
									{:else if part.state === 'failed'}
										<Icon name="warning" size={13} />
									{:else if running(part) && part.state !== 'queued'}
										<Spinner size={13} />
									{:else}
										·
									{/if}
								</span>
							{/if}
							<span class="flex min-w-0 flex-1 flex-col gap-0.5">
								<span class="flex flex-wrap items-center gap-2">
									<span class="text-lead font-semibold">{t(text.name)}</span>
									{#if needed(part, parts)}
										<Badge>{t('models.needed')}</Badge>
									{/if}
								</span>
								<span class="text-label text-muted">{t(text.text)}</span>
								{#if loaded && part.state === 'downloading'}
									<Progress
										value={percent(part)}
										label={t('models.line.percent', {
											percent: percent(part),
											size: size(part.download_size, locale)
										})}
										class="mt-1"
									/>
								{/if}
								{#if part.error && part.state !== 'installed'}
									<span role="alert" class="text-label text-red-ink">
										{t(errorKey(part.error.code))}
									</span>
								{/if}
							</span>
							<span class={cn('min-w-32 text-right text-label tabular-nums', target.tone)}>
								{target.text}
							</span>
						</li>
					{/each}
				</ul>
			{/if}
			<div class="flex flex-wrap items-center justify-between gap-3 bg-surface-2 px-5 py-3.5">
				<span class="flex items-center gap-2 text-label text-muted">
					<Icon name="badge" size={15} />{t('setup.checksum')}
				</span>
				<SourceChoice id="setup-source" label={t('models.source')} withFile />
			</div>
		</section>

		{#if models.error && !models.running}
			<div
				role="alert"
				class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-line bg-red-0 px-5 py-3.5 text-red-ink"
			>
				<span class="flex items-center gap-2">
					<Icon name="warning" size={16} />{t(errorKey(models.error.code))}
				</span>
				<Button size="sm" onclick={() => (loaded = null)}>{t('setup.toTheSelection')}</Button>
			</div>
		{/if}

		{#if loaded === null || (!models.running && !neededPresent)}
			<div class="flex flex-wrap items-center justify-between gap-4">
				<Button variant="link" icon="folder" disabled={models.running} onclick={fromFile}>
					{t('setup.fromFile')}
				</Button>
				{#if overview && ids.length === 0 && neededPresent}
					<Button
						variant="primary"
						size="lg"
						iconRight="next"
						disabled={!ready}
						busy={!ready}
						onclick={() => goto(resolve('/(app)'))}
					>
						{t('setup.getStarted')}
					</Button>
				{:else}
					<Button
						variant="primary"
						size="lg"
						icon="herunterladen"
						disabled={!overview || ids.length === 0 || onlyFile || models.running}
						onclick={start}
					>
						{onlyFile ? t('setup.onlyFile') : t('setup.start', { size: size(rest, locale) })}
					</Button>
				{/if}
			</div>
		{:else}
			<div
				role="status"
				class="flex flex-wrap items-center justify-between gap-4 rounded-lg border-[1.5px] border-accent bg-tint-1 px-5 py-4 halo-accent"
			>
				<span class="flex flex-col gap-0.5">
					<span class="font-semibold">
						{neededPresent ? t('setup.ready') : t('setup.loads')}
					</span>
					<span class="text-label text-muted">
						{#if neededPresent && stillInBackground}
							{t('setup.ready.background')}
						{:else if neededPresent}
							{t('setup.ready.all')}
						{:else}
							{t('setup.loads.text')}
						{/if}
					</span>
				</span>
				<span class="flex flex-wrap gap-2">
					{#if models.running}
						<Button onclick={cancel}>{t('setup.cancel')}</Button>
					{/if}
					<Button
						variant="primary"
						iconRight="next"
						disabled={!ready}
						busy={neededPresent && !ready}
						onclick={() => goto(resolve('/(app)'))}
					>
						{t('setup.getStarted')}
					</Button>
				</span>
			</div>
		{/if}

		<details class="rounded-lg border border-line bg-surface px-4.5 py-3.5">
			<summary class="cursor-pointer font-semibold">{t('setup.whatWasLoaded')}</summary>
			<div class="mt-3">
				<Checksums components={parts} />
			</div>
		</details>

		<div class="flex flex-wrap items-center justify-between gap-3">
			<p class="flex items-center gap-2 text-label text-subtle">
				<Icon name="lock" size={15} />{t('setup.network')}
			</p>
			{#if !ready}
				<Button variant="link" size="sm" href={resolve('/(app)')}>
					{t('setup.later')}
				</Button>
			{/if}
		</div>
	</div>
</div>
