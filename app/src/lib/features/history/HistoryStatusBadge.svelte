<script lang="ts">
	import Badge, { type BadgeTone } from '#lib/ui/Badge.svelte';
	import type { HistoryStatus } from '#lib/history/history.js';
	import { statusTone } from '#lib/history/history.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * The workflow status of an entry (plan 3.2). "Gespeichert" is green,
	 * "Kopiert" stays quiet;
	 * review and unsaved changes use the warning treatment, processing the
	 * progress treatment, failures the error treatment. The label always
	 * shows, so color never carries the status alone.
	 */
	let { status }: { status: HistoryStatus } = $props();

	const TONE: Record<ReturnType<typeof statusTone>, BadgeTone> = {
		quiet: 'neutral',
		warning: 'amber',
		progress: 'tint',
		error: 'red'
	};
</script>

<Badge tone={status === 'saved' ? 'green' : TONE[statusTone(status)]}
	>{t(`history.status.${status}`)}</Badge
>
