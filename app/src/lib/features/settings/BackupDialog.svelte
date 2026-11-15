<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';
	import Field from '#lib/ui/Field.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { errorSentence, backupName } from '#lib/settings/dialog.js';
	import type { VaultStatusResponse } from '#lib/sidecar/api.gen.js';
	import { importBackup, saveBackup } from '#lib/sidecar/settings.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { errorCode } from './change.js';

	/**
	 * Export or import a backup (translation table).
	 *
	 * The backup includes both the table and the keys (real names) and is
	 * created only with a password. Importing replaces the keys and table of
	 * this device – the dialog explains this before a file is chosen.
	 */
	const MINIMUM_LENGTH = 10;

	let {
		open = $bindable(false),
		kind,
		mappings,
		onimported
	}: {
		open?: boolean;
		kind: 'export' | 'import';
		/** Mappings on this device that will be replaced by an import. */
		mappings: number;
		onimported: (status: VaultStatusResponse) => void;
	} = $props();

	let password = $state('');
	let repetition = $state('');
	let error = $state<string | null>(null);
	let running = $state(false);

	$effect(() => {
		if (!open) {
			password = '';
			repetition = '';
			error = null;
		}
	});

	const toShort = $derived(password.length > 0 && password.length < MINIMUM_LENGTH);
	const unequal = $derived(kind === 'export' && repetition.length > 0 && repetition !== password);
	const ready = $derived(
		kind === 'export'
			? password.length >= MINIMUM_LENGTH && repetition === password
			: password.length > 0
	);

	async function run(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!ready || running) return;
		running = true;
		error = null;
		try {
			if (kind === 'export') {
				const path = await saveBackup(
					password,
					t('settings.table.backup.dialog'),
					backupName(new Date())
				);
				if (path === null) return;
				toast(t('settings.table.backup.saved'));
			} else {
				const status = await importBackup(password, {
					title: t('settings.table.import.dialog'),
					filter: t('settings.table.import.filter')
				});
				if (status === null) return;
				onimported(status);
				toast(t('settings.table.import.done', { n: status.mapping_count }));
			}
			open = false;
		} catch (reason) {
			error = errorSentence(errorCode(reason), t);
		} finally {
			running = false;
		}
	}
</script>

<Dialog
	bind:open
	width="sm"
	title={kind === 'export' ? t('settings.table.backup.title') : t('settings.table.import.title')}
	description={kind === 'export'
		? t('settings.table.backup.text')
		: t('settings.table.import.text')}
>
	<form id="backup-formular" class="flex flex-col gap-4" onsubmit={run}>
		{#if kind === 'import'}
			<ul
				class="flex flex-col gap-1.5 rounded-md border border-red-line-soft bg-red-0 px-4 py-3 text-ui text-ink-2"
			>
				<li class="flex gap-2">
					<span aria-hidden="true" class="text-red-ink">–</span>{t(
						'settings.table.import.sequence1'
					)}
				</li>
				{#if mappings > 0}
					<li class="flex gap-2">
						<span aria-hidden="true" class="text-red-ink">–</span>{t(
							'settings.table.import.sequence2',
							{
								n: mappings
							}
						)}
					</li>
				{/if}
			</ul>
		{/if}
		<Field
			label={t('settings.table.password')}
			notice={kind === 'export'
				? t('settings.table.password.notice', { n: MINIMUM_LENGTH })
				: undefined}
			error={toShort && kind === 'export' ? t('settings.error.passwordShort') : undefined}
		>
			{#snippet children({ id, describedby, invalid })}
				<TextInput
					{id}
					type="password"
					autocomplete={kind === 'export' ? 'new-password' : 'current-password'}
					aria-describedby={describedby}
					{invalid}
					bind:value={password}
				/>
			{/snippet}
		</Field>
		{#if kind === 'export'}
			<Field
				label={t('settings.table.password.repeat')}
				error={unequal ? t('settings.table.password.unequal') : undefined}
			>
				{#snippet children({ id, describedby, invalid })}
					<TextInput
						{id}
						type="password"
						autocomplete="new-password"
						aria-describedby={describedby}
						{invalid}
						bind:value={repetition}
					/>
				{/snippet}
			</Field>
		{/if}
		{#if error}<p role="alert" class="text-label text-red-ink">{error}</p>{/if}
	</form>
	{#snippet fuss()}
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.cancel')}</Button>
		<Button
			type="submit"
			form="backup-formular"
			variant={kind === 'export' ? 'primary' : 'danger'}
			disabled={!ready}
			busy={running}
		>
			{kind === 'export' ? t('settings.table.backup.go') : t('settings.table.import.go')}
		</Button>
	{/snippet}
</Dialog>
