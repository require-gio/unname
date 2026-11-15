<script lang="ts">
	import DecidedRow from '#lib/features/review/DecidedRow.svelte';
	import QuestionCard from '#lib/features/review/QuestionCard.svelte';
	import SelectionMenu from '#lib/features/review/SelectionMenu.svelte';
	import type { Segment } from '#lib/ui/segments.js';
	import { LABELS } from '#lib/sidecar/result-policy.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import MaskLabel from '#lib/ui/MaskLabel.svelte';
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import { LABEL_DISPLAY } from '#lib/ui/labels.js';
	import Chapter from './Chapter.svelte';

	/**
	 * Reimplementation of the “Bausteine” board from the mockup (1440 × 900):
	 * question card, addendum via marking, labels and the original code.
	 * All components here are real building blocks—not just placeholders for this page.
	 */

	const nothing = () => {};

	const ADDENDUM: Segment[] = [
		{ kind: 'plain', text: 'Das Holz stammt wie vereinbart von den ' },
		{
			kind: 'code',
			text: '<<PERSON_77c0a2f1>>',
			id: 'f1',
			type: 'PERSON',
			original: 'Gebrüdern Wachtelhuber',
			operable: true
		},
		{ kind: 'plain', text: '. Ein Muster der Arbeitsplatte in ' },
		{ kind: 'ai-kept', text: 'Eiche Natur' },
		{ kind: 'plain', text: ' liegt bei, wie von Ihrer Tochter ' },
		{ kind: 'selection', text: 'Lena' },
		{ kind: 'plain', text: ' gewünscht.' }
	];

	const PEEK: Segment[] = [
		{ kind: 'plain', text: 'Sehr geehrte Frau ' },
		{
			kind: 'code',
			text: '<<PERSON_51c2e8d4>>',
			id: 'peek',
			type: 'PERSON',
			original: 'Hofer',
			operable: true
		},
		{ kind: 'plain', text: ',' }
	];

	let kind = $state<'name' | 'place' | 'company' | 'number' | 'other'>('name');
</script>

<div
	data-testid="board-components"
	class="flex h-[900px] w-[1440px] flex-col gap-7 overflow-hidden bg-bg px-12 py-11 text-ink"
>
	<div class="flex items-baseline gap-4.5">
		<h1 class="font-serif text-display font-normal">Bausteine</h1>
		<p class="text-muted">
			Fragekarte, Nachtragen per Markierung, Etiketten und das Original am Code
		</p>
	</div>

	<div class="grid min-h-0 flex-1 grid-cols-[420px_470px_1fr] gap-7">
		<div class="flex flex-col gap-3">
			<Chapter title="Fragekarte · KI schlägt vor">
				<QuestionCard
					number={1}
					kind="suspicion"
					value="Gebrüdern Wachtelhuber"
					ai={{
						safety: 0.84,
						text: 'Name eines Familienbetriebs – eher eine persönliche Angabe.',
						suggestion: 'mask'
					}}
					onmask={nothing}
					onkeep={nothing}
				/>
			</Chapter>
			<Chapter title="Fragekarte · KI ist unsicher" class="mt-1.5">
				<QuestionCard
					value="Linde"
					context={{ before: 'die alte ', after: ' hinter dem Haus wurde gefällt' }}
					ai={{ safety: 0.52, text: 'Unklar – Baum oder Nachname. Bitte selbst entscheiden.' }}
					onmask={nothing}
					onkeep={nothing}
				/>
			</Chapter>
			<Chapter title="Entschieden" class="mt-1.5">
				<DecidedRow value="Muster" result="keep" onaction={nothing} />
			</Chapter>
		</div>

		<Chapter title="Fehlende Stelle nachtragen">
			<div class="relative h-[430px] overflow-hidden rounded-lg bg-canvas">
				<div
					class="absolute top-7 -right-10 -bottom-5 left-7 rounded-xs bg-surface px-8.5 py-7.5 font-doc text-doc text-doc-ink shadow-page"
				>
					<p data-testid="addendum-text">
						<SegmentText segments={ADDENDUM} onsegment={nothing} peek={false} />
					</p>
				</div>
				<SelectionMenu
					text="Lena"
					bind:kind
					occurrence={4}
					files={3}
					onmask={nothing}
					oncancel={nothing}
					class="absolute top-[150px] left-[150px]"
				/>
			</div>
			<p class="text-label text-pretty text-muted">
				Text markieren genügt – ein kleines Menü fragt nur nach der Art. Gleiche Stellen werden in
				allen geladenen Dateien gleich behandelt.
			</p>
		</Chapter>

		<div class="flex flex-col gap-3">
			<Chapter title="Etiketten">
				<ul class="flex flex-col rounded-lg border border-line bg-surface">
					{#each LABELS as label (label)}
						<li
							class="flex flex-col gap-1.25 border-b border-line-soft px-4 py-3.25 last:border-b-0"
						>
							<MaskLabel {label} />
							<span class="text-label text-muted">{t(LABEL_DISPLAY[label].explanation)}</span>
							<span class="text-fine text-subtle"
								>{t('label.inReviewReport', {
									technicalTerm: t(LABEL_DISPLAY[label].technicalTerm)
								})}</span
							>
						</li>
					{/each}
				</ul>
			</Chapter>
			<Chapter title="Original am Code · Mauszeiger darauf" class="mt-2.5">
				<div
					class="relative rounded-lg border border-line bg-surface px-5.5 pt-16 pb-6.5 font-doc text-doc text-doc-ink"
				>
					<p><SegmentText segments={PEEK} onsegment={nothing} peekOpen="peek" /></p>
					<p class="mt-2.5 font-sans text-meta text-subtle">
						Spart das Umschalten auf »Original«. Gilt in »Ergebnis« und in der rechten Spalte von
						»Nebeneinander«.
					</p>
				</div>
			</Chapter>
		</div>
	</div>
</div>
