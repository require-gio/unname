<script lang="ts">
	import { resolve } from '$app/paths';
	import { cn } from '#lib/utils.js';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import { GATE_TEXT, gateNotice } from '#lib/sidecar/gate-notice.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Show the local processing state above the content only when something is locked.
	 * Start state is quiet, failure state is red – otherwise the alarm becomes background noise.
	 */
	const notice = $derived(
		gateNotice({ snapshot: sidecarGate.snapshot, outside: sidecarGate.outside })
	);
	let restartRunning = $state(false);

	async function restart(): Promise<void> {
		restartRunning = true;
		try {
			await sidecarGate.restart();
		} finally {
			restartRunning = false;
		}
	}
</script>

{#if notice}
	{@const text = GATE_TEXT[notice.position]}
	<div class="mx-auto max-w-[960px] px-10 pt-5">
		<div
			role="status"
			class={cn(
				'flex items-center gap-3 rounded-lg border px-4 py-3 text-ui',
				notice.position === 'failure'
					? 'border-red-line bg-red-0 text-red-ink'
					: 'border-line bg-surface text-muted'
			)}
		>
			{#if notice.position === 'starts'}
				<Spinner size={16} class="text-link" />
			{:else}
				<Icon name={notice.position === 'failure' ? 'warning' : 'info'} size={16} />
			{/if}
			<p class="min-w-0 flex-1">
				<span class={cn('font-semibold', notice.position !== 'failure' && 'text-ink')}
					>{t(text.title)}</span
				>
				· {t(text.text)}
			</p>
			{#if notice.restart}
				<Button size="sm" busy={restartRunning} onclick={restart}>{t('gate.restart')}</Button>
			{:else if notice.position === 'setup'}
				<Button size="sm" href={resolve('/setup')}>{t('gate.setup.open')}</Button>
			{/if}
		</div>
	</div>
{/if}
