<script lang="ts">
	import SetupView from '#lib/features/models/SetupView.svelte';
	import { t } from '#lib/state/i18n.svelte.js';
</script>

<svelte:head>
	<title>{t('setup.pageTitle')} · {t('app.name')}</title>
</svelte:head>

<!-- Board "0 · Initial setup" – no sidebar (initial setup). -->
<SetupView />
