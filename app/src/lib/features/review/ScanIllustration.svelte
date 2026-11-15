<script lang="ts">
	import { cn } from '#lib/utils.js';
	import Icon from '#lib/ui/Icon.svelte';
	import type { IconName } from '#lib/ui/icons.js';
	import type { DetectionFinding } from '#lib/review/detection.js';

	/**
	 * The board sheet “2 · detection running”: rows that light up one by one,
	 * with a scanning line above. Purely decorative; when motion is reduced
	 * everything stays still.
	 *
	 * When detection finishes, the scan stops: the lit spots remain and a
	 * symbol for the finding appears on the sheet – questions open, everything
	 * protected, nothing found or failed.
	 */
	let { finding = 'running' }: { finding?: DetectionFinding } = $props();

	type Line = { width: string; place?: { width: string; verzug: string; question?: boolean } }[];

	const LINES: (Line | 'gap')[] = [
		[{ width: 'w-[34%]' }, { width: '', place: { width: 'w-[44%]', verzug: '' } }],
		[{ width: 'w-[52%]' }],
		'gap',
		[{ width: '', place: { width: 'w-[40%]', verzug: '[animation-delay:0.35s]' } }],
		[{ width: 'w-[46%]' }],
		'gap',
		[{ width: 'w-[92%]' }],
		[
			{ width: 'w-[30%]' },
			{ width: '', place: { width: 'w-[32%]', verzug: '[animation-delay:0.7s]' } },
			{ width: 'w-[24%]' }
		],
		[{ width: 'w-[86%]' }],
		[
			{ width: 'w-[46%]' },
			{ width: '', place: { width: 'w-[36%]', verzug: '[animation-delay:1.05s]', question: true } }
		],
		[{ width: 'w-[78%]' }],
		'gap',
		[
			{ width: 'w-[22%]' },
			{ width: '', place: { width: 'w-[58%]', verzug: '[animation-delay:1.4s]' } }
		],
		[{ width: 'w-[64%]' }],
		[{ width: 'w-[70%]' }]
	];

	const CHARACTER: Record<
		Exclude<DetectionFinding, 'running'>,
		{ icon: IconName; tone: string }
	> = {
		questions: { icon: 'badgeQuestion', tone: 'bg-amber-1 text-amber-ink-2 ring-amber-line' },
		found: { icon: 'badge', tone: 'bg-green-1 text-green-ink ring-green-2' },
		nothing: { icon: 'haken', tone: 'bg-tint-2 text-link ring-line' },
		error: { icon: 'warning', tone: 'bg-red-1 text-red-ink ring-red-line' }
	};

	const running = $derived(finding === 'running');
	/** No markings when there are no findings – otherwise the sheet would contradict the finding. */
	const withPlaces = $derived(
		finding === 'running' || finding === 'questions' || finding === 'found'
	);
	const character = $derived(finding === 'running' ? null : CHARACTER[finding]);
</script>

<div aria-hidden="true" class="flex max-w-full flex-[0_0_320px] justify-center pt-3">
	<div class="relative">
		<div
			class={cn(
				'relative flex h-97.5 w-72.5 -rotate-2 flex-col gap-3 overflow-hidden rounded-sm bg-surface px-7.5 py-8.5 shadow-page transition-opacity duration-500',
				finding === 'error' && 'opacity-60'
			)}
		>
			{#each LINES as line, index (index)}
				{#if line === 'gap'}
					<span class="block h-3.5"></span>
				{:else}
					<span class="flex gap-1.5">
						{#each line as part, j (j)}
							{#if part.place && withPlaces}
								<span
									class={cn(
										'block h-2 origin-left rounded-[3px]',
										running && 'motion-safe:animate-auftauchen',
										running && part.place.verzug,
										part.place.width,
										part.place.question && finding !== 'found' ? 'bg-amber-strong' : 'bg-accent'
									)}
								></span>
							{:else}
								<span
									class={cn('block h-2 rounded-[3px] bg-skeleton', part.width || part.place?.width)}
								></span>
							{/if}
						{/each}
					</span>
				{/if}
			{/each}
			{#if running}
				<span
					class="absolute inset-x-0 top-[-12%] h-16 border-b-[1.5px] border-accent bg-linear-to-b from-transparent via-accent/15 to-accent/55 motion-safe:animate-scan motion-reduce:hidden"
				></span>
			{/if}
		</div>
		{#if character}
			{#key finding}
				<span
					class={cn(
						'motion-safe:animate-stempel absolute -right-4 -bottom-4 inline-flex size-19 items-center justify-center rounded-full ring-4 shadow-page',
						character.tone
					)}
				>
					<Icon name={character.icon} size={36} strokeWidth={1.8} />
				</span>
			{/key}
		{/if}
	</div>
</div>
