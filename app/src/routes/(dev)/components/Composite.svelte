<script lang="ts">
	import { SEGMENT_KINDS, type Segment } from '#lib/ui/segments.js';
	import { Masking } from '#lib/state/masking.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import HistoryStatusBadge from '#lib/features/history/HistoryStatusBadge.svelte';
	import RecentEntries from '#lib/features/shell/RecentEntries.svelte';
	import type { HistoryStatus } from '#lib/history/history.js';
	import { lastExamples } from './history-examples.js';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import ComponentList, { type Component } from '#lib/ui/ComponentList.svelte';
	import FileRow from '#lib/ui/FileRow.svelte';
	import MaskLabel from '#lib/ui/MaskLabel.svelte';
	import MaskingControl from '#lib/ui/MaskingControl.svelte';
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import Select from '#lib/ui/Select.svelte';
	import SettingRow from '#lib/ui/SettingRow.svelte';
	import StatGrid from '#lib/ui/StatGrid.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import Chapter from './Chapter.svelte';

	const LAST = $derived(lastExamples(i18n.language, t));
	const STATES: HistoryStatus[] = ['processing', 'review', 'failed', 'changed', 'saved', 'copied'];

	const start = new Masking('reversible');
	const review = new Masking('reversible');
	const standard = new Masking('reversible');

	/** Example for each segment kind – legend of the style map. */
	const EXAMPLE: Record<(typeof SEGMENT_KINDS)[number], Segment> = {
		plain: { kind: 'plain', text: 'Fließtext' },
		code: {
			kind: 'code',
			text: '<<PERSON_ba903855>>',
			id: 'c',
			type: 'PERSON',
			original: 'Sabine Hofer',
			operable: true
		},
		placeholder: {
			kind: 'placeholder',
			text: '[Name]',
			id: 'p',
			type: 'PERSON',
			original: 'Sabine Hofer',
			operable: true
		},
		'masked-original': {
			kind: 'masked-original',
			text: 'Sabine Hofer',
			id: 'm',
			type: 'PERSON',
			operable: true
		},
		question: { kind: 'question', text: 'Wachtelhuber', id: 'q', number: 1 },
		'kept-category': {
			kind: 'kept-category',
			text: 'Linz',
			id: 'k',
			type: 'LOCATION',
			operable: true
		},
		'kept-here': { kind: 'kept-here', text: 'Muster', id: 'h', type: 'PERSON', operable: true },
		'ai-kept': { kind: 'ai-kept', text: 'Eiche Natur' },
		restored: { kind: 'restored', text: 'Markus Leitner' },
		'restored-other': { kind: 'restored-other', text: 'Renate Wimmer' },
		'unknown-code': { kind: 'unknown-code', text: '<<PERSON_8e21d7f0>>' },
		'pattern-hit': { kind: 'pattern-hit', text: '[Aktenzeichen]' },
		selection: { kind: 'selection', text: 'Lena' }
	};

	const COMPONENTS: Component[] = [
		{
			id: 'ner',
			name: 'Namenserkennung',
			description: 'Findet Namen, Firmen und Orte – in allen Sprachen.',
			size: '350 MB',
			on: true,
			locked: true,
			badge: { text: t('component.needed'), tone: 'neutral' },
			status: 'done'
		},
		{
			id: 'de',
			name: 'Deutsch',
			description: 'Wortlisten, Datums- und Betragsformate, Texterkennung für deutsche Scans',
			size: '25 MB',
			on: true,
			status: 'done'
		},
		{
			id: 'ocr',
			name: 'Texterkennung für Fotos und Scans',
			description: 'Liest Text aus Bildern und gescannten PDFs',
			size: '30 MB',
			on: true,
			status: 'loads',
			progress: 64
		},
		{
			id: 'ai',
			name: 'Zweitmeinung bei unsicheren Stellen',
			description: 'Kleines KI-Modell, das Grenzfälle einschätzt',
			size: '280 MB',
			on: false,
			badge: { text: t('component.recommended'), tone: 'tint' },
			status: 'skipped'
		}
	];

	let selection = $state(COMPONENTS.map((part) => ({ ...part })));
	let mark = $state(true);
	let retention = $state('30');
	let active = $state<string>();
</script>

<div class="flex flex-col gap-10">
	<div class="grid grid-cols-[468px_1fr] gap-10">
		<Chapter title="MaskingControl · kompakt (Prüfen-Menü)">
			<Card radius="xl" class="shadow-pop" gap="md" data-testid="masking-review">
				<MaskingControl
					masking={review}
					categories
					eyebrow
					count={{
						finance_ids: 2,
						names: 6,
						addresses: 2,
						contact: 2,
						numbers: 1,
						organizations: 1,
						locations: 1,
						dates: 3
					}}
					replaced={t('masking.places', { n: 13 })}
					usability={t('masking.share', { share: 9 })}
					scope={t('masking.applies.entry', { n: 3 })}
				>
					{#snippet reference()}<a href="#components">{t('common.changeStandard')}</a>{/snippet}
				</MaskingControl>
			</Card>
		</Chapter>
		<div class="flex flex-col gap-10">
			<Chapter title="MaskingControl · kompakt (Startseite)">
				<Card radius="xl" class="w-[390px] shadow-pop">
					<MaskingControl masking={start} scope={t('masking.applies.new')}>
						{#snippet reference()}<a href="#components">{t('common.moreSettings')}</a>{/snippet}
					</MaskingControl>
				</Card>
			</Chapter>
			<Chapter title="MaskingControl · Karten (Einstellungen)">
				<MaskingControl masking={standard} variant="cards" />
			</Chapter>
		</div>
	</div>

	<Chapter title="SegmentText · alle Segmentarten">
		<div class="grid grid-cols-4 gap-x-6 gap-y-3 rounded-lg border border-line bg-surface p-5">
			{#each SEGMENT_KINDS as kind (kind)}
				<div class="flex flex-col gap-1">
					<span class="font-mono text-fine text-subtle">{kind}</span>
					<span class="font-doc text-doc text-doc-ink">
						<SegmentText
							segments={[EXAMPLE[kind]]}
							onsegment={(segment) => (active = active === segment.id ? undefined : segment.id)}
							{active}
							{mark}
						/>
					</span>
				</div>
			{/each}
		</div>
		<label class="inline-flex items-center gap-2.5 self-start text-label text-muted">
			Eingesetzte Stellen markieren
			<Switch bind:checked={mark} label="Eingesetzte Stellen markieren" />
		</label>
	</Chapter>

	<div class="grid grid-cols-2 gap-10">
		<Chapter title="FileRow · Karte (Erkennung)">
			<ul class="flex flex-col gap-2.5">
				<FileRow variant="map" name="Rechnung_2026-0418.pdf" status="done" progress={100}>
					{#snippet metaContent()}
						1 Seite · 15 Stellen gefunden · <strong class="font-semibold text-amber-ink-2"
							>2 brauchen Ihren Blick</strong
						>
					{/snippet}
				</FileRow>
				<FileRow
					variant="map"
					name="Vollmacht_Scan.jpg"
					status="running"
					statusText="Text wird aus dem Bild gelesen"
					progress={64}
					meta="Foto eines Dokuments · bisher 3 Stellen gefunden"
					hervorgehoben
				/>
			</ul>
			<StatGrid
				title={t('detection.previousFound')}
				values={[
					{ num: 12, label: t('category.names') },
					{ num: 5, label: t('category.addresses') },
					{ num: 4, label: t('category.contact') },
					{ num: 3, label: t('category.numbers') }
				]}
			/>
		</Chapter>
		<Chapter title="FileRow · Liste (Gespeichert), kompakt (Zurückübersetzen)">
			<ul class="flex flex-col border-t border-line-soft">
				{#each ['Rechnung_2026-0418_geschützt.pdf', 'Angebot_Hofer_geschützt.docx'] as name (name)}
					<FileRow {name} meta="13 Stellen maskiert">
						{#snippet right()}
							<MaskLabel label="reversible" />
							<a href="#components" class="text-label font-medium text-link">{t('common.view')}</a>
						{/snippet}
					</FileRow>
				{/each}
				<FileRow
					name="Vollmacht_Scan_geschützt.txt"
					kind="image"
					meta="6 Stellen maskiert · Text aus dem Bild"
				>
					{#snippet right()}<MaskLabel label="irreversible" />{/snippet}
				</FileRow>
			</ul>
			<ul class="flex flex-col gap-2">
				<FileRow
					variant="kompakt"
					tone="tint"
					name="Zahlungserinnerung_Entwurf_mit_Namen.docx"
					meta="neue Datei · die Datei der KI bleibt unverändert"
				>
					{#snippet right()}
						<Button size="sm">Öffnen</Button>
						<Button variant="primary" size="sm">Speichern unter …</Button>
					{/snippet}
				</FileRow>
			</ul>
		</Chapter>
	</div>

	<div class="grid grid-cols-[256px_1fr_1fr] gap-10">
		<Chapter title="RecentEntries · HistoryStatusBadge">
			<div class="rounded-lg bg-side p-2.5">
				<RecentEntries rows={LAST} active="1" />
			</div>
			<div class="flex flex-wrap gap-2">
				{#each STATES as status (status)}<HistoryStatusBadge {status} />{/each}
			</div>
		</Chapter>
		<Chapter title="SettingRow">
			<SettingRow title="Einträge im Verlauf automatisch löschen" forTarget="retention-demo">
				<Select
					id="retention-demo"
					bind:value={retention}
					options={[
						{ value: '7', label: 'nach 7 Tagen' },
						{ value: '30', label: 'nach 30 Tagen' },
						{ value: 'never', label: 'never' }
					]}
				/>
			</SettingRow>
			<SettingRow
				title="Sprache jedes Dokuments automatisch erkennen"
				description="Absatz für Absatz – auch gemischte Dokumente funktionieren."
			>
				{#snippet children({ titleId })}<Switch checked labelledby={titleId} size="md" />{/snippet}
			</SettingRow>
			<SettingRow
				title="Neu beginnen"
				description="Ein neuer Schlüssel oder eine leere Tabelle machen alle bisherigen Codes unlesbar."
				tone="danger"
			>
				<Button variant="danger">Neuer Schlüssel …</Button>
				<Button variant="danger">Tabelle leeren …</Button>
			</SettingRow>
		</Chapter>
		<Chapter title="ComponentList · Auswahl und Laden">
			<ComponentList
				title="Was soll auf dieses Gerät?"
				total="Gesamt 685 MB · frei: 212 GB"
				components={selection}
				ontoggle={(id) =>
					(selection = selection.map((part) =>
						part.id === id ? { ...part, on: !part.on } : part
					))}
			/>
			<ComponentList
				title="Wird geladen und geprüft"
				total="2 von 4 fertig"
				components={COMPONENTS}
				mode="load"
			/>
		</Chapter>
	</div>
</div>
