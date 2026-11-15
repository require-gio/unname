<script lang="ts">
	import { Toaster } from 'svelte-sonner';
	import Icon from './Icon.svelte';
	import Spinner from './Spinner.svelte';

	/**
	 * Shows brief notifications ("Kopiert", "Gespeichert") at bottom-center
	 * using the bar colour. Calls `toast()` from svelte-sonner; appearance
	 * comes solely from the token classes – no extra colour layer.
	 */
</script>

<Toaster
	position="bottom-center"
	offset={24}
	visibleToasts={3}
	toastOptions={{
		unstyled: true,
		classes: {
			toast:
				'flex w-[356px] items-start gap-2.5 rounded-lg bg-bar px-4 py-3 font-sans text-ui text-bar-ink shadow-bar',
			title: 'font-semibold',
			description: 'mt-0.5 text-meta text-bar-muted',
			icon: 'mt-0.5 text-bar-accent',
			actionButton:
				'ml-auto rounded-sm bg-bar-on px-2.5 py-1 text-label font-semibold text-bar-on-ink',
			cancelButton: 'ml-auto rounded-sm px-2.5 py-1 text-label text-bar-muted',
			closeButton: 'text-bar-muted'
		}
	}}
>
	{#snippet loadingIcon()}<Spinner size={16} />{/snippet}
	{#snippet successIcon()}<Icon name="haken" size={16} />{/snippet}
	{#snippet errorIcon()}<Icon name="warning" size={16} />{/snippet}
	{#snippet infoIcon()}<Icon name="info" size={16} />{/snippet}
	{#snippet warningIcon()}<Icon name="warning" size={16} />{/snippet}
</Toaster>
