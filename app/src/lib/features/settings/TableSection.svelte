<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Select from '#lib/ui/Select.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import { clearVault, fetchVault, rotateVault } from '#lib/sidecar/api-v2.js';
	import type { VaultStatusResponse } from '#lib/sidecar/api.gen.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { change, showError, gateConnection } from './change.js';
	import BackupDialog from './BackupDialog.svelte';

	/**
	 * Settings › Translation table: metrics, retention for unused data,
	 * backup and “Start over”. All actions that invalidate existing codes
	 * go through a dialog that explains the consequences.
	 */
	type Retention = '6' | '12' | 'never';

	let status = $state<VaultStatusResponse | null>(null);
	let error = $state(false);
	let backup = $state<'export' | 'import' | null>(null);
	let newKey = $state(false);
	let clear = $state(false);
	let running = $state<'key' | 'clear' | null>(null);

	$effect(() => {
		const connection = sidecarGate.connection;
		if (!connection) return;
		fetchVault(connection)
			.then((answer) => {
				status = answer;
				error = false;
			})
			.catch(() => (error = true));
	});

	const retention = $derived<Retention>(
		(() => {
			const months = settings.values?.vault?.unused_retention_months;
			return months === null ? 'never' : months === 6 ? '6' : '12';
		})()
	);
	const retentions = $derived([
		{ value: '6' as const, label: t('settings.table.months', { n: 6 }) },
		{ value: '12' as const, label: t('settings.table.months', { n: 12 }) },
		{ value: 'never' as const, label: t('settings.never') }
	]);
	const keyOfThe = $derived(
		status?.created_at
			? new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'de-DE', {
					dateStyle: 'long'
				}).format(new Date(status.created_at))
			: null
	);

	async function selectRetention(value: Retention): Promise<void> {
		const months = value === 'never' ? null : (Number(value) as 6 | 12);
		if (await change({ vault: { unused_retention_months: months } })) {
			const connection = sidecarGate.connection;
			if (connection) status = await fetchVault(connection).catch(() => status);
		}
	}

	async function startOver(kind: 'key' | 'clear'): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		running = kind;
		try {
			status = kind === 'key' ? await rotateVault(connection) : await clearVault(connection);
			toast(kind === 'key' ? t('settings.table.key.done') : t('settings.table.clear.done'));
		} catch (reason) {
			showError(reason);
		} finally {
			running = null;
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-6">
	{#if error}
		<p role="status" class="text-subtle">{t('settings.table.notAvailable')}</p>
	{:else if !status}
		<Skeleton class="h-22" />
	{:else}
		<div class="flex items-center gap-4 rounded-lg border border-line px-5 py-4">
			<span class="font-serif text-display-lg leading-none tabular-nums"
				>{status.mapping_count}</span
			>
			<span class="flex flex-col">
				<span class="font-semibold">
					{t('settings.table.mappings', {
						n: status.mapping_count,
						entries: status.entry_count ?? 0
					})}
				</span>
				<span class="text-label text-muted">
					{keyOfThe
						? t('settings.table.protected.date', { date: keyOfThe })
						: t('settings.table.protected')}
				</span>
			</span>
		</div>
	{/if}
	<p class="text-pretty text-muted">{t('settings.table.text')}</p>
	<SettingRow title={t('settings.table.retention')} forTarget="table-retention">
		<Select
			id="table-retention"
			value={retention}
			options={retentions}
			onchange={(event) => void selectRetention(event.currentTarget.value as Retention)}
		/>
	</SettingRow>
	<SettingRow title={t('settings.table.keep')} description={t('settings.table.keep.text')}>
		<Button size="sm" icon="save" onclick={() => (backup = 'export')}>
			{t('settings.table.backup')}
		</Button>
		<Button size="sm" icon="upload" onclick={() => (backup = 'import')}>
			{t('settings.table.import')}
		</Button>
	</SettingRow>
	<SettingRow
		tone="danger"
		title={t('settings.table.new')}
		description={t('settings.table.new.text')}
	>
		<Button size="sm" variant="danger" busy={running === 'key'} onclick={() => (newKey = true)}>
			{t('settings.table.key')}
		</Button>
		<Button
			size="sm"
			variant="danger"
			busy={running === 'clear'}
			disabled={status?.mapping_count === 0}
			onclick={() => (clear = true)}
		>
			{t('settings.table.clear')}
		</Button>
	</SettingRow>
</div>

<BackupDialog
	bind:open={() => backup !== null, (open) => !open && (backup = null)}
	kind={backup ?? 'export'}
	mappings={status?.mapping_count ?? 0}
	onimported={(fresh) => (status = fresh)}
/>
<ConfirmDialog
	bind:open={newKey}
	danger
	title={t('settings.table.key.title')}
	description={t('settings.table.key.text')}
	sequences={[
		t('settings.table.sequence.codes'),
		t('settings.table.sequence.mappings', { n: status?.mapping_count ?? 0 }),
		t('settings.table.sequence.newCodes')
	]}
	confirm={t('settings.table.key.ja')}
	onconfirm={() => void startOver('key')}
/>
<ConfirmDialog
	bind:open={clear}
	danger
	title={t('settings.table.clear.title')}
	description={t('settings.table.clear.text')}
	sequences={[
		t('settings.table.sequence.codes'),
		t('settings.table.sequence.mappings', { n: status?.mapping_count ?? 0 }),
		t('settings.table.sequence.sameCodes')
	]}
	confirm={t('settings.table.clear.ja')}
	onconfirm={() => void startOver('clear')}
/>
