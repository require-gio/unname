<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Select from '#lib/ui/Select.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import { fileSize } from '#lib/format.js';
	import { clearEntries } from '#lib/sidecar/api-v2.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { history } from '#lib/state/history.svelte.js';
	import { change, showError, gateConnection } from './change.js';

	/**
	 * Settings › History: retention period for auto-deletion and the
	 * “clear history” action. Deleting removes entries, their file lists,
	 * and stored originals; the translation table is kept.
	 * Also frees the space occupied by the draft state.
	 */
	type Retention = '7' | '30' | '90' | 'never';

	const retention = $derived<Retention>(
		settings.historyRetention === null ? 'never' : (String(settings.historyRetention) as Retention)
	);
	const retentions = $derived([
		{ value: '7' as const, label: t('settings.history.days', { n: 7 }) },
		{ value: '30' as const, label: t('settings.history.days', { n: 30 }) },
		{ value: '90' as const, label: t('settings.history.days', { n: 90 }) },
		{ value: 'never' as const, label: t('settings.never') }
	]);
	let confirm = $state(false);
	let clears = $state(false);

	// Refresh on open: storage usage changes after each load.
	$effect(() => {
		void history.update();
	});
	const storage = $derived(history.storage);
	const used = $derived(
		storage
			? t('settings.history.space.value', {
					n: storage.entries.length,
					size: fileSize(storage.total_bytes)
				})
			: null
	);
	const free = $derived(
		storage?.free_bytes != null
			? t('settings.history.space.free', { free: fileSize(storage.free_bytes) })
			: null
	);

	function selectRetention(value: Retention): void {
		const days = value === 'never' ? null : (Number(value) as 7 | 30 | 90);
		void change({ history: { retention_days: days } }).then(() => history.update());
	}

	async function clear(): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		clears = true;
		try {
			await clearEntries(connection);
			await history.update();
			toast(t('settings.history.cleared'));
			if (page.params.id) settings.toTheStartPage(resolve('/(app)'));
		} catch (error) {
			showError(error);
		} finally {
			clears = false;
		}
	}
</script>

<div class="flex max-w-190 flex-col gap-6">
	<SettingRow title={t('settings.history.space')} description={t('settings.history.space.text')}>
		<span class="flex flex-col items-end gap-0.5 text-right">
			{#if used}
				<span class="text-ui font-semibold text-ink tabular-nums" data-testid="storage-used"
					>{used}</span
				>
				{#if free}<span class="text-caption text-subtle tabular-nums">{free}</span>{/if}
			{:else if history.loaded}
				<span class="text-caption text-subtle">{t('settings.history.space.error')}</span>
			{/if}
		</span>
	</SettingRow>
	<SettingRow title={t('settings.history.retention')} forTarget="history-retention">
		<Select
			id="history-retention"
			value={retention}
			options={retentions}
			onchange={(event) => selectRetention(event.currentTarget.value as Retention)}
		/>
	</SettingRow>
	<p class="text-pretty text-muted">{t('settings.history.text')}</p>
	<Button
		variant="danger"
		class="self-start"
		busy={clears}
		disabled={history.entries.length === 0}
		onclick={() => (confirm = true)}
	>
		{t('settings.history.clear')}
	</Button>
</div>

<ConfirmDialog
	bind:open={confirm}
	danger
	title={t('settings.history.clear.title')}
	description={t('settings.history.clear.text')}
	sequences={[
		t('settings.history.clear.sequence1', { n: history.entries.length }),
		t('settings.history.clear.sequence2'),
		t('settings.history.clear.sequence3')
	]}
	confirm={t('settings.history.clear.ja')}
	onconfirm={() => void clear()}
/>
