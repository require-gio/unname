"""Lazy lifecycle management of phase-1 services."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .analysis import LanguageAnalysis, PlainTextAnalyzer, ProfileAnalyzer
from .assessments import SecondOpinionOptions, term_check_for, with_assessments
from .audit import AuditLog
from .base_profile import BASE_PROFILE_ID, base_profile
from .config import Settings
from .detection import (
    DetectionPipeline,
    DetectionResult,
    EntityThresholds,
    NerModelUnavailableError,
    build_ner_adapter,
    select_ner_hardware,
)
from .detection.second_opinion import SecondOpinionModel
from .detection.word_classes import SpacyWordClassTagger, TermCheck
from .detection.wordlists import load_language_wordlists, load_wordlist, normalize_terms
from .deterministic_recognizers import create_deterministic_recognizers
from .diagnostics import DiagnosticsRecorder
from .draft_jobs import load_job
from .drafts import DraftError, DraftStore
from .entries import Entry, EntryStore
from .extraction import DocumentExtractor, ExtractedDocument, LimitedOcrBackend, default_ocr_backend
from .extraction.ocr import APPLE_VISION, OcrBackend, OcrUnavailableError, ocr_engine_name
from .hardware import (
    DeviceSelection,
    HardwareBackend,
    HardwareCapabilities,
    TorchHardwareBackend,
    detect_hardware,
    runtime_fallback,
    select_device,
)
from .jobs import JobManager
from .languages import DocumentLanguageOptions, packs
from .processing import TextProcessor
from .profiles import (
    GpuMode,
    NlpEngine,
    Profile,
    ProfileRuleViolation,
    RefreshMode,
    default_profiles_directory,
    load_profiles,
    profile_by_id,
)
from .qos import QosClass, set_thread_qos
from .registry import (
    MasterDataRegistry,
    RegistryError,
    RegistryImportError,
    RegistrySources,
    RegistryStatus,
)
from .registry_crypto import load_or_create_registry_key
from .rendering import JobMemoryCache
from .resources import (
    BundledAssetError,
    cached_model_components,
    classifier_location,
    ocr_location,
    selected_ocr_engine,
    setup_required,
    spacy_available,
    spacy_model_reference,
)
from .restored_files import RestoredFileStore
from .runtime import device_status as device_status_fallback
from .settings_store import SettingsStore
from .vault import TokenVault
from .vocabulary import VocabularyStore
from .workers import WorkerCapacity, capacity_for, ocr_slots_for

#: “Unused entries are deleted after 6/12 months” – expressed in days.
_DAYS_PER_MONTH = 30.44


class PhaseOneServices:
    """Create models, registry and pools only when needed."""

    def __init__(
        self,
        settings: Settings,
        diagnostics: DiagnosticsRecorder,
        *,
        registry: MasterDataRegistry | None = None,
        analyzer: PlainTextAnalyzer | None = None,
        jobs: JobManager | None = None,
        vault: TokenVault | None = None,
        processor: TextProcessor | None = None,
        extractor: DocumentExtractor | None = None,
        profiles: tuple[Profile, ...] | None = None,
        profile_resources_root: Path | None = None,
        audit_log: AuditLog | None = None,
        entries: EntryStore | None = None,
        settings_store: SettingsStore | None = None,
        vocabulary: VocabularyStore | None = None,
        drafts: DraftStore | None = None,
        capacity: WorkerCapacity | None = None,
        hardware_backend: HardwareBackend | None = None,
        hardware_capabilities: HardwareCapabilities | None = None,
    ) -> None:
        self.settings = settings
        self.diagnostics = diagnostics
        self.capacity = capacity or capacity_for()
        self._registry = registry
        self._analyzer = analyzer
        self._jobs = jobs
        self._vault = vault
        self._processor = processor
        self._extractor = extractor
        self._profiles = profiles
        self._profile_resources_root = (
            Path(profile_resources_root)
            if profile_resources_root is not None
            else default_profiles_directory().parent
        )
        self._profile_registries: dict[str, MasterDataRegistry] = {}
        self._profile_source_signatures: dict[
            str,
            tuple[tuple[str, int, int, int, int, int] | None, ...],
        ] = {}
        #: Last observed master-data state per profile. Used by the UI to answer
        #: “Is this profile currently running without level 0?” without triggering
        #: an import.
        self._profile_master_data_states: dict[str, dict[str, Any]] = {}
        self._audit_log = audit_log
        self._entries = entries
        self._settings_store = settings_store
        self._vocabulary = vocabulary
        self._drafts = drafts
        #: The draft loader is attached to the JobManager once it exists.
        self._loader_installed = False
        self._drafts_startup: threading.Thread | None = None
        self._second_opinion_model: SecondOpinionModel | None = None
        #: Cache of recent text-mode detections per SHA-256 of the text. “Copy”
        #: should not re-detect what the live preview already detected.
        #: Stored only in memory, few entries.
        self._text_detections: OrderedDict[str, DetectionResult] = OrderedDict()
        #: Staging folder per entry until the shell publishes the saved files
        #: (``POST /entries/{id}/outputs``). Includes folder, timestamp and saved data.
        self._stagings: dict[str, tuple[Path, float, Any]] = {}
        #: Restored files kept in memory until the shell retrieves them – they
        #: contain real filenames.
        self._restored_files = RestoredFileStore()
        #: Page images and layout cards for the review view – plain text, kept in
        #: memory and released with the job (spec §4.1).
        self._job_memory: JobMemoryCache | None = None
        self._entries_janitor_stop = threading.Event()
        self._entries_janitor: threading.Thread | None = None
        self._hardware_backend = hardware_backend or TorchHardwareBackend()
        self._hardware_capabilities = hardware_capabilities
        self._device_selection: DeviceSelection | None = None
        self._lock = threading.RLock()
        #: Separate from ``_lock`` because it is only for short dictionary accesses.
        #: Threads that only record a status should not wait for model construction.
        self._status_lock = threading.Lock()
        #: A master-data import per profile runs under its own lock.
        #: Previously it used ``_lock`` – that blocked every status query and the
        #: live-review of the shell.
        self._profile_import_locks: dict[str, threading.Lock] = {}
        #: Most recent fully computed master-data snapshot. ``health_snapshot``
        #: reads it instead of building a registry for a query.
        self._last_master_data_status: dict[str, Any] | None = None
        self._watcher_stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._warmup: threading.Thread | None = None
        #: Phase U: the OCR backend for this platform, selected once.
        self._ocr_backend: OcrBackend | None = None
        self._ocr_warmup: threading.Thread | None = None
        self._ocr_warmup_ms: int | None = None
        self._source_signature: tuple[tuple[str, int, int, int, int, int] | None, ...] | None = None
        self._last_reload_error: str | None = None
        self._last_checked_at: datetime | None = None

    @property
    def registry_path(self) -> Path:
        return self.settings.state_directory / "master-data.registry.enc"

    @property
    def key_path(self) -> Path:
        return self.settings.state_directory / "master-data.registry.key"

    def master_data(self) -> MasterDataRegistry:
        with self._lock:
            if self._registry is None:
                _prepare_private_directory(self.settings.state_directory)
                key = load_or_create_registry_key(self.key_path)
                self._registry = MasterDataRegistry(
                    self.registry_path,
                    key,
                    nightly_sources=self.configured_sources(),
                )
            return self._registry

    def analyzer(self) -> PlainTextAnalyzer:
        with self._lock:
            if self._analyzer is None:
                thresholds = EntityThresholds(dict(self.settings.ner_thresholds))
                selection = self.device_selection()
                pipeline = DetectionPipeline(
                    ner=build_ner_adapter(
                        select_ner_hardware(
                            self.capacity.memory_bytes,
                            active_device=selection.active,
                        ),
                        thresholds=thresholds,
                        on_fallback=self._record_ner_fallback,
                    ),
                    thresholds=thresholds,
                    # Word classes come from language packs; without a pack the
                    # detection stays unchanged.
                    word_classes=SpacyWordClassTagger(_spacy_tagger_reference),
                )
                self._analyzer = PlainTextAnalyzer(
                    self.master_data(),
                    semantic_pipeline=pipeline,
                    diagnostics=self.diagnostics,
                )
            return self._analyzer

    def analyzer_for_profile(self, profile: Profile) -> ProfileAnalyzer:
        analyzer = self.analyzer()
        # Test/integration runners can provide their own profile semantics.
        # Production always uses the encrypted, profile-specific registry.
        second_opinion = (
            self._second_opinion_stage(profile) if profile.id == BASE_PROFILE_ID else None
        )
        term_check = self._term_check() if profile.id == BASE_PROFILE_ID else None
        if not isinstance(analyzer, PlainTextAnalyzer):
            return ProfileAnalyzer(
                analyzer,  # type: ignore[arg-type]
                profile,
                second_opinion=second_opinion,
            )
        self._assert_profile_runtime_supported(profile)
        master_data: Any = self._master_data_for_profile(profile)
        languages: LanguageAnalysis | None = None
        if profile.id == BASE_PROFILE_ID:
            # Document languages and countries from settings.
            # Countries select numbers, IDs, phone regions; languages select
            # language packs. Applies to all future detections.
            options = self.document_language_options()
            deterministic = create_deterministic_recognizers(
                countries=options.countries,
                languages=options.languages,
            )
            languages = self.language_analysis(options)
            # Names and terms apply to the new UI: name lists and terms
            # alongside master data (level 0), patterns alongside built-in rules.
            names, patterns = self.vocabulary().recognizers()
            master_data = _WithExtraRecognizers(master_data, names)
            deterministic = (*deterministic, *patterns)
        else:
            deterministic = create_deterministic_recognizers(phone_regions=profile.phone_regions)
        return ProfileAnalyzer(
            analyzer,
            profile,
            master_data=master_data,
            deterministic_recognizers=deterministic,
            sentence_starters=self._profile_sentence_starters(profile),
            languages=languages,
            second_opinion=second_opinion,
            term_check=term_check,
        )

    def _term_check(self) -> TermCheck | None:
        """Second opinion for matches without proper names (``word_classes.py``)."""

        options = self.second_opinion_options()
        model = self.second_opinion_model() if options is not None else None
        if options is None or model is None:
            return None
        return term_check_for(model, options)

    def second_opinion_options(self) -> SecondOpinionOptions | None:
        """Second opinion settings; ``None`` if disabled."""

        current = self.settings_store().get().second_opinion
        if not current.enabled:
            return None
        return SecondOpinionOptions(policy=current.policy, keep_threshold=current.keep_threshold)

    def second_opinion_model(self) -> SecondOpinionModel | None:
        """Classifier on this device (lazy loaded).

        A newly installed folder is discovered on the next call; a different
        folder replaces the loaded model.
        """

        folder = classifier_location().path
        with self._lock:
            if folder is None:
                self._second_opinion_model = None
            elif self._second_opinion_model is None or self._second_opinion_model.folder != folder:
                self._second_opinion_model = SecondOpinionModel(
                    folder, threads=max(1, min(2, os.cpu_count() or 1))
                )
            return self._second_opinion_model

    def _second_opinion_stage(
        self, profile: Profile
    ) -> Callable[[str, DetectionResult], DetectionResult] | None:
        options = self.second_opinion_options()
        model = self.second_opinion_model() if options is not None else None
        if options is None or model is None:
            return None

        def stage(text: str, detection: DetectionResult) -> DetectionResult:
            return with_assessments(
                text, detection, profile, model, options, diagnostics=self.diagnostics
            )

        return stage

    def document_language_options(self) -> DocumentLanguageOptions:
        """Document languages and countries from settings."""

        current = self.settings_store().get().document_languages
        return DocumentLanguageOptions(
            languages=current.languages,
            per_paragraph=current.per_paragraph,
            countries=current.countries,
        )

    def language_analysis(self, options: DocumentLanguageOptions) -> LanguageAnalysis:
        """Language packs for detection: function words, sentence starters, and stopwords."""

        chosen = packs(options.languages)
        wordlists = {item.code: load_language_wordlists(item.code) for item in chosen}
        return LanguageAnalysis(
            function_words={item.code: normalize_terms(item.function_words) for item in chosen},
            default=options.default_language,
            per_paragraph=options.per_paragraph,
            sentence_starters={code: lists.sentence_starters for code, lists in wordlists.items()},
            business_terms={code: lists.business_terms for code, lists in wordlists.items()},
            thresholds={
                item.code: EntityThresholds(dict(item.thresholds))
                for item in chosen
                if item.thresholds
            },
        )

    def _assert_profile_runtime_supported(self, profile: Profile) -> None:
        if (
            profile.execution.nlp_engine is not NlpEngine.GLINER
            or profile.execution.spacy_fallback != "de_core_news_lg"
            # Device selection is checked once per sidecar process; it is not re-evaluated per
            # profile.
            # Explicit overrides use PRESIDIO_DEVICE, so profiles must stay on ``auto``.
            or profile.execution.gpu is not GpuMode.AUTO
            or profile.execution.cpu_workers != "auto"
        ):
            raise ProfileRuleViolation(
                "profile_runtime_unsupported",
                "Das Profil fordert eine in dieser Sidecar-Version nicht "
                "verfuegbare Ausfuehrungskonfiguration.",
            )

    def _profile_sentence_starters(
        self,
        profile: Profile,
    ) -> frozenset[str]:
        if not profile.suspect_check.enabled:
            return frozenset()
        resource = profile.suspect_check.stopwords_file
        assert resource is not None
        path = self._profile_resources_root / resource
        if not path.is_file():
            raise ProfileRuleViolation(
                "profile_resource_unavailable",
                "Die profilgebundene Verdachtswortliste ist nicht verfuegbar.",
            )
        return load_wordlist(path)

    def _profile_import_lock(self, profile_id: str) -> threading.Lock:
        with self._status_lock:
            lock = self._profile_import_locks.get(profile_id)
            if lock is None:
                lock = threading.Lock()
                self._profile_import_locks[profile_id] = lock
            return lock

    def _master_data_for_profile(self, profile: Profile) -> MasterDataRegistry:
        if not profile.master_data.enabled:
            self._note_master_data_state(profile, ready=True, reason=None, count=0)
            return self.master_data()

        with self._lock:
            registry = self._profile_registries.get(profile.id)
            if registry is None:
                _prepare_private_directory(self.settings.state_directory)
                registry = MasterDataRegistry(
                    self.settings.state_directory / f"master-data.{profile.id}.registry.enc",
                    load_or_create_registry_key(self.key_path),
                    min_length=profile.master_data.min_length,
                )
                self._profile_registries[profile.id] = registry

        # From here the ``_lock`` is deliberately released. An import reads and encrypts
        # whole customer lists; under the shared lock the service is paused for a few seconds —
        # long enough for the shell to consider it dead and abort the running job.
        with self._profile_import_lock(profile.id):
            return self._refresh_profile_master_data(profile, registry)

    def _refresh_profile_master_data(
        self,
        profile: Profile,
        registry: MasterDataRegistry,
    ) -> MasterDataRegistry:
        persons_file = profile.master_data.persons_file
        organizations_file = profile.master_data.orgs_file
        assert persons_file is not None and organizations_file is not None
        sources = RegistrySources(
            self._profile_resources_root / persons_file,
            self._profile_resources_root / organizations_file,
        )
        source_signature = _sources_signature(sources)
        sources_available = all(item is not None for item in source_signature)
        previous_signature = self._profile_source_signatures.get(profile.id)
        status = registry.status()
        should_refresh = sources_available and (
            status.imported_at is None
            or (
                profile.master_data.refresh is RefreshMode.DAILY
                and source_signature != previous_signature
            )
        )
        if should_refresh:
            try:
                registry.reimport(sources)
            except (OSError, RegistryError) as error:
                if status.imported_at is None:
                    # Files are present but cannot be read reliably. This is an integrity issue
                    # and must fail closed: a corrupted state must not be treated as
                    # "no master data" (spec §2).
                    raise ProfileRuleViolation(
                        "master_data_unavailable",
                        "Die an das Profil gebundenen Stammdaten sind nicht sicher verfuegbar.",
                    ) from error
            else:
                self._profile_source_signatures[profile.id] = source_signature
                self._note_master_data_state(
                    profile,
                    ready=True,
                    reason=None,
                    count=registry.status().total_count,
                )
                return registry

        profile_status = registry.status()
        if profile_status.imported_at is not None:
            self._note_master_data_state(
                profile,
                ready=True,
                reason=None,
                count=profile_status.total_count,
            )
            return registry

        # An explicit state loaded via the import API can serve as a manual profile binding.
        global_registry = self.master_data()
        global_count = global_registry.status().total_count
        if global_count:
            self._note_master_data_state(
                profile,
                ready=True,
                reason=None,
                count=global_count,
            )
            return global_registry

        # From here on we know there is no source on this device.
        # This is the normal case for a fresh installation, not an error.
        # Level 0 is omitted; levels 1-3 (rules, NER, suspicion check) run unchanged.
        # The state is shown (start view, diagnostics, review report) so no one assumes
        # a higher hit rate from the master-data level (spec §6.2, 11).
        self._note_master_data_state(
            profile,
            ready=False,
            reason="sources_missing",
            count=0,
            sources=sources,
        )
        return registry

    def _note_master_data_state(
        self,
        profile: Profile,
        *,
        ready: bool,
        reason: str | None,
        count: int,
        sources: RegistrySources | None = None,
    ) -> None:
        """Record which master-data state this profile is currently using."""

        state: dict[str, Any] = {
            "enabled": profile.master_data.enabled,
            "ready": ready,
            "total_count": count,
            "reason": reason,
            "expected_sources": (
                [str(sources.persons), str(sources.organizations)] if sources is not None else []
            ),
        }
        with self._status_lock:
            previous = self._profile_master_data_states.get(profile.id)
            self._profile_master_data_states[profile.id] = state
        # Only the change is logged; repeated identical lines in diagnostics are ignored.
        if previous == state or reason is None:
            return
        self.diagnostics.record(
            "master_data_missing",
            "Ohne Stammdaten verarbeitet: Stufe 0 entfaellt, Regeln, NER und "
            "Verdachtspruefung laufen unveraendert.",
            severity="warning",
            profile=profile.id,
            reason=reason,
            expected_sources=state["expected_sources"],
        )

    def profile_master_data_state(self, profile: Profile) -> dict[str, Any]:
        """Master-data state of a profile without triggering an import."""

        with self._status_lock:
            known = self._profile_master_data_states.get(profile.id)
        if known is not None:
            return dict(known)

        if not profile.master_data.enabled:
            return {
                "enabled": False,
                "ready": True,
                "total_count": 0,
                "reason": None,
                "expected_sources": [],
            }

        persons_file = profile.master_data.persons_file
        organizations_file = profile.master_data.orgs_file
        assert persons_file is not None and organizations_file is not None
        sources = RegistrySources(
            self._profile_resources_root / persons_file,
            self._profile_resources_root / organizations_file,
        )
        available = all(item is not None for item in _sources_signature(sources))
        # A state previously loaded via the import API counts the same.
        global_count = self.master_data_status()["total_count"]
        ready = bool(available or global_count)
        return {
            "enabled": True,
            "ready": ready,
            "total_count": int(global_count) if not available else 0,
            "reason": None if ready else "sources_missing",
            "expected_sources": [str(sources.persons), str(sources.organizations)],
        }

    def jobs(self) -> JobManager:
        with self._lock:
            if self._jobs is not None and not self._loader_installed:
                self._install_loader(self._jobs)
            if self._jobs is None:
                if not self.master_data_usable():
                    raise RuntimeError("Konfigurierte Stammdaten sind nicht sicher verfuegbar.")
                worker_count = self.settings.cpu_workers or self.capacity.cpu_workers
                self._jobs = JobManager(
                    self.analyzer(),
                    max_workers=worker_count,
                    ttl_seconds=self.settings.job_ttl_seconds,
                    timeout_seconds=self.settings.job_timeout_seconds,
                    max_text_bytes=self.settings.max_text_bytes,
                    on_failed=lambda error: self.diagnostics.record(
                        "job_failed",
                        "Ein Analysejob ist fehlgeschlagen; es wird kein Teilergebnis ausgegeben.",
                        severity="error",
                        error_type=type(error).__name__,
                    ),
                    on_hook_failed=lambda hook, error: self.diagnostics.record(
                        "draft_write_failed",
                        "Ein Zwischenstand liess sich nicht in den Arbeitsstand schreiben.",
                        severity="warning",
                        hook=hook,
                        error_type=type(error).__name__,
                    ),
                )
                self._install_loader(self._jobs)
            return self._jobs

    def _install_loader(self, jobs: JobManager) -> None:
        # If a job is not in memory, load it from the persisted state.
        jobs.set_loader(lambda job_id: load_job(self, job_id))
        self._loader_installed = True

    def drafts(self) -> DraftStore:
        """Encrypted persisted store for document entries.

        Located in the app data folder (not in the cache that macOS may clear),
        under ``drafts.noindex`` – excluded from Spotlight and backups.
        """

        with self._lock:
            if self._drafts is None:
                _prepare_private_directory(self.settings.state_directory)
                root = self.settings.drafts_directory or (
                    self.settings.state_directory / "drafts.noindex"
                )
                self._drafts = DraftStore(root, self.settings.state_directory / "drafts.key")
            return self._drafts

    def job_memory(self) -> JobMemoryCache:
        jobs = self.jobs()
        with self._lock:
            if self._job_memory is None:
                cache = JobMemoryCache()
                jobs.on_removed(cache.forget)
                self._job_memory = cache
            return self._job_memory

    def vault(self) -> TokenVault:
        """Shared token vault for the lifetime of the sidecar process."""

        with self._lock:
            if self._vault is None:
                self._vault = TokenVault(self.settings.state_directory)
            return self._vault

    def processor(self) -> TextProcessor:
        with self._lock:
            if self._processor is None:
                self._processor = TextProcessor(self.vault())
            return self._processor

    def ocr_backend(self) -> OcrBackend:
        """OCR backend selection: Vision on macOS, otherwise Tesseract."""

        with self._lock:
            if self._ocr_backend is None:
                self._ocr_backend = default_ocr_backend()
            return self._ocr_backend

    def extractor(self) -> DocumentExtractor:
        with self._lock:
            if self._extractor is None:
                backend = self.ocr_backend()
                self._extractor = DocumentExtractor(
                    ocr_backend=LimitedOcrBackend(
                        backend,
                        parallel_slots=ocr_slots_for(ocr_engine_name(backend), self.capacity),
                    )
                )
            return self._extractor

    def profiles(self) -> tuple[Profile, ...]:
        """Load the profile catalog atomically; a broken profile aborts startup."""

        with self._lock:
            if self._profiles is None:
                self._profiles = load_profiles()
            return self._profiles

    def profile(self, profile_id: str) -> Profile:
        if profile_id == BASE_PROFILE_ID:
            return base_profile()
        return profile_by_id(profile_id, self.profiles())

    def settings_store(self) -> SettingsStore:
        with self._lock:
            if self._settings_store is None:
                _prepare_private_directory(self.settings.state_directory)
                self._settings_store = SettingsStore(
                    self.settings.state_directory / "settings.json"
                )
            return self._settings_store

    def vocabulary(self) -> VocabularyStore:
        """Encrypted store for name lists, custom terms, patterns and templates."""

        with self._lock:
            if self._vocabulary is None:
                _prepare_private_directory(self.settings.state_directory)
                self._vocabulary = VocabularyStore(
                    self.settings.state_directory / "vocabulary.enc",
                    self.settings.state_directory / "vocabulary.key",
                )
            return self._vocabulary

    def purge_unused_mappings(self) -> int:
        """Retention for the translation table: delete long-unused entries."""

        months = self.settings_store().get().vault.unused_retention_months
        if months is None:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=round(months * _DAYS_PER_MONTH))
        return self.vault().purge_unused(older_than=cutoff)

    def apply_retention(self) -> None:
        """Apply both retention policies immediately after settings change."""

        self.entries()
        self.purge_expired_entries()
        self.purge_unused_mappings()

    def entries(self) -> EntryStore:
        """Encrypted history; expired entries are removed on open."""

        with self._lock:
            if self._entries is None:
                _prepare_private_directory(self.settings.state_directory)
                self._entries = EntryStore(
                    self.settings.state_directory / "entries.enc",
                    self.settings.state_directory / "entries.key",
                )
                created = True
            else:
                created = False
            store = self._entries
        if created:
            self.purge_expired_entries()
        return store

    def purge_expired_entries(self) -> int:
        """Retention janitor: deletes expired entries together with jobs and draft."""

        store = self._entries
        if store is None:
            return 0
        retention = self.settings_store().get().history.retention_days
        candidates = [entry.id for entry in store.expired(retention)]
        if not candidates:
            return 0
        removed = self._purge(
            candidates,
            lambda: store.purge_expired(retention, only=candidates),
        )
        return len(removed)

    def delete_entry(self, entry_id: str) -> Entry:
        """“Delete entry”: entry, jobs and all saved originals (3.6)."""

        store = self.entries()
        store.get(entry_id)
        removed = self._purge([entry_id], lambda: [store.delete(entry_id)])
        return removed[0]

    def clear_entries(self) -> list[Entry]:
        """„Verlauf leeren": like delete, but for all entries at once."""

        store = self.entries()
        candidates = [entry.id for entry in store.list()]
        if not candidates:
            return []
        return self._purge(candidates, lambda: store.delete_many(candidates))

    def _purge(
        self,
        entry_ids: Collection[str],
        remove: Callable[[], list[Entry]],
    ) -> list[Entry]:
        """Single path for deletion (spec §3.6).

        1. Lock: marker so no later worker writes; cancel jobs, wait for leases.
        2. Make unreadable: remove entry and key atomically from history.
        3. Free memory: jobs, staging folder, page images, provisioning.
        4. Free disk: delete the entry's state.
        5. If step 3 or 4 fails, sweep_drafts cleans up empty folders at start and hourly.
        """

        drafts = self.drafts()
        locked: list[str] = []
        try:
            for entry_id in entry_ids:
                drafts.tombstone(entry_id)
                locked.append(entry_id)
            jobs = self._jobs
            if jobs is not None:
                for entry_id in entry_ids:
                    jobs.forget_entry(entry_id)
            removed = remove()
        except BaseException:
            for entry_id in locked:
                drafts.untombstone(entry_id)
            raise
        removed_ids = {entry.id for entry in removed}
        for entry_id in set(entry_ids) - removed_ids:
            # Selection changed (retention) or already gone: leave untouched.
            drafts.untombstone(entry_id)
        for entry in removed:
            self.release_staging(entry.id)
            try:
                drafts.delete_entry(entry.id)
            except (DraftError, OSError) as error:
                # Remaining part is already unreadable (key missing); cleanup later.
                self.diagnostics.record(
                    "drafts_cleanup",
                    "Der Arbeitsstand eines geloeschten Eintrags wird spaeter entfernt.",
                    severity="warning",
                    error_type=type(error).__name__,
                )
        return removed

    def sweep_drafts(self) -> int:
        """Cleaner: staging areas without entry, without file, leftover ``*.tmp``."""

        store = self.entries()
        known = {
            entry.id: {item.id for item in entry.files}
            for entry in store.list()
            if store.draft_key(entry.id) is not None
        }
        return self.drafts().sweep(known)

    def _text_detection_key(self, text: str) -> str:
        # Languages and countries are part of the key: changing settings re-detects same text.
        # Also second opinion (spec §K): on/off, policy and model availability.
        options = self.document_language_options().fingerprint()
        opinion = self.second_opinion_options()
        model = self.second_opinion_model() if opinion is not None else None
        second = f"{opinion.fingerprint()}:{model.folder}" if opinion and model else "-"
        return hashlib.sha256(f"{options}\x00{second}\x00{text}".encode()).hexdigest()

    def remember_text_detection(self, text: str, detection: DetectionResult) -> None:
        key = self._text_detection_key(text)
        with self._status_lock:
            self._text_detections[key] = detection
            self._text_detections.move_to_end(key)
            while len(self._text_detections) > 4:
                self._text_detections.popitem(last=False)

    def cached_text_detection(self, text: str) -> DetectionResult | None:
        key = self._text_detection_key(text)
        with self._status_lock:
            return self._text_detections.get(key)

    def remember_staging(self, entry_id: str, staging: Path, details: Any = None) -> None:
        """Records the provisioning folder of a save; older ones are discarded.

        ``details`` describes what was saved (masking, draft state);
        ``record_outputs`` writes it to the export history. Folders not retrieved
        after the job TTL are cleaned up on the next call.
        """

        now = time.monotonic()
        with self._status_lock:
            previous = self._stagings.pop(entry_id, None)
            stale = [
                key
                for key, (_, created, _details) in self._stagings.items()
                if now - created > self.settings.job_ttl_seconds
            ]
            removed = [self._stagings.pop(key)[0] for key in stale]
            self._stagings[entry_id] = (staging, now, details)
        if previous is not None:
            removed.append(previous[0])
        self._remove_stagings(removed)

    def staged_save(self, entry_id: str) -> Any:
        """Return the data saved by the most recent unsaved staging."""

        with self._status_lock:
            item = self._stagings.get(entry_id)
        return item[2] if item is not None else None

    def release_staging(self, entry_id: str) -> None:
        with self._status_lock:
            item = self._stagings.pop(entry_id, None)
        if item is not None:
            self._remove_stagings([item[0]])

    def _remove_stagings(self, stagings: list[Path]) -> None:
        jobs = self._jobs
        for staging in stagings:
            try:
                if jobs is not None:
                    jobs.remove_staging(staging)
            except (OSError, RuntimeError):
                continue

    def restored_files(self) -> RestoredFileStore:
        return self._restored_files

    def audit_log(self) -> AuditLog:
        with self._lock:
            if self._audit_log is None:
                _prepare_private_directory(self.settings.state_directory)
                self._audit_log = AuditLog(self.settings.state_directory / "export-audit.sqlite3")
            return self._audit_log

    def worker_health(self) -> dict[str, str | bool | None]:
        # Intentionally lock-free: see health_snapshot.
        jobs = self._jobs
        if jobs is None:
            return {"healthy": True, "reason": None}
        return jobs.health_status()

    def _jobs_busy(self) -> bool:
        # Intentionally lock-free: see health_snapshot.
        jobs = self._jobs
        return jobs is not None and jobs.busy

    def health_snapshot(self) -> dict[str, Any]:
        """Health report for ``GET /health`` – no lock, no init, no I/O.

        This endpoint is the live check of the shell. It must not wait on
        ``_lock`` or start a service. Previously the check failed because
        during the first document the registry and model were loading, the
        load held the lock, the check timed out, and the shell reported the
        service as dead, aborting the running job. The second attempt
        succeeded once the load had completed.

        Fields are written exactly once and then never changed. A lock-free
        read therefore sees either the old or the new value, never a half-way
        state.
        """

        analyzer = self._analyzer
        selection = self._device_selection
        models = cached_model_components()
        return {
            # models are present; the shell locks afterwards
            # “Deposit” until name detection and a language are ready.
            "models": models,
            "setup_required": setup_required(models),
            "nlp_engine": (
                analyzer.nlp_status() if analyzer is not None else self._pending_nlp_status()
            ),
            "master_data": self._master_data_status_cached(),
            "workers": self.worker_status(),
            "device": (
                selection.as_dict() if selection is not None else self._pending_device_status()
            ),
            "job_worker": self.worker_health(),
            # Phase T3: keep the shell active while jobs run or wait,
            # preventing App Nap and sleep.
            "busy": self._jobs_busy(),
            # While the registry and model are loading, the service is busy
            # and must not be disturbed. The shell must keep them separate.
            "warming_up": self._warmup is not None and self._warmup.is_alive(),
            # Phase U: which OCR engine is used, for settings › technique.
            "ocr": self.ocr_status(available=models["ocr"]["state"] == "ready"),
        }

    def ocr_status(self, *, available: bool | None = None) -> dict[str, Any]:
        """OCR status for ``/health`` – no child process, cached only.

        ``state``: ``ready``, ``warming`` (Vision compiles on first run after
        install or system update ≈ 50 s) or ``missing``.
        """

        engine = selected_ocr_engine()
        # ``/health`` returns the cached state instead of checking again.
        if available is None:
            try:
                available = ocr_location().path is not None
            except (OSError, BundledAssetError):
                available = False
        status: dict[str, Any] = {"engine": engine, "state": "ready" if available else "missing"}
        if engine == APPLE_VISION:
            from .extraction.ocr_vision import cached_helper_info

            info = cached_helper_info()
            status.update(
                {
                    "request": "text",
                    "revision": 3,
                    "helper": info.helper if info is not None else None,
                    "os_version": info.os_version if info is not None else None,
                    "os_build": info.os_build if info is not None else None,
                    "languages": ["de-DE", "en-US"],
                }
            )
            if available and self._ocr_warmup is not None and self._ocr_warmup.is_alive():
                status["state"] = "warming"
        else:
            backend = self._ocr_backend
            engine_info = getattr(backend, "engine", None)
            status.update(
                {
                    "version": getattr(engine_info, "version", None),
                    "languages": ["deu", "eng"],
                }
            )
        status["warmup_ms"] = self._ocr_warmup_ms
        return status

    def record_ocr_document(self, document: ExtractedDocument) -> None:
        """Per OCR document, without content: engine, request, revision, system."""

        if not document.ocr_used:
            return
        status = self.ocr_status()
        self.diagnostics.record(
            "ocr",
            "Texterkennung verwendet.",
            engine=status["engine"],
            request=status.get("request"),
            revision=status.get("revision"),
            os_version=status.get("os_version"),
            os_build=status.get("os_build"),
            helper=status.get("helper"),
            version=status.get("version"),
            ocr_pages=len(document.ocr_pages),
        )

    def _pending_device_status(self) -> dict[str, Any]:
        """Device state while the one-time probe has not yet run.

        The desire comes from the settings frozen at startup and must not be
        overwritten by later changes to ``PRESIDIO_DEVICE``.
        """

        return {
            **device_status_fallback(),
            "requested": self.settings.presidio_device,
            "mps_fallback_env": self.settings.mps_fallback_enabled,
        }

    def _pending_nlp_status(self) -> dict[str, Any]:
        """Model state while no analyzer has been built."""

        selection = self._device_selection
        hardware = select_ner_hardware(
            self.capacity.memory_bytes,
            active_device=selection.active if selection is not None else "cpu",
        )
        return {
            "preferred": "gliner",
            "fallback": "de_core_news_lg",
            "selected": "gliner" if hardware.prefer_gliner else "de_core_news_lg",
            "active": None,
            "loaded": False,
            "load_policy": "lazy_local_only",
            "device": hardware.active_device if hardware.prefer_gliner else "cpu",
        }

    def _master_data_status_cached(self) -> dict[str, Any]:
        """Last known master-data state without building a registry."""

        registry = self._registry
        if registry is not None:
            status = registry.status()
        else:
            cached = self._last_master_data_status
            if cached is not None:
                return dict(cached)
            status = RegistryStatus(0, 0, None)
        return {
            **status.as_dict(),
            "usable": self._master_data_usable_for_status(status),
            "configured_nightly_import": self.settings.master_data_import_path is not None,
            "last_checked_at": (
                self._last_checked_at.isoformat() if self._last_checked_at is not None else None
            ),
            "last_reload_error": self._last_reload_error,
        }

    def configured_sources(self) -> RegistrySources | None:
        configured = self.settings.master_data_import_path
        if configured is None:
            return None
        if configured.suffix.casefold() == ".json":
            # A combined JSON may contain both separate lists.
            return RegistrySources(configured, configured)
        if configured.is_dir() or not configured.suffix:
            return RegistrySources.from_directory(configured)
        raise RegistryImportError(
            "Ein einzelner Nightly-Pfad muss ein kombiniertes JSON oder ein "
            "Verzeichnis mit personen.csv und firmen.csv sein."
        )

    def master_data_status(self) -> dict[str, Any]:
        registry = self._registry
        if registry is None and not self.registry_path.exists():
            status = RegistryStatus(0, 0, None)
        else:
            status = self.master_data().status()
        result = {
            **status.as_dict(),
            "usable": self._master_data_usable_for_status(status),
            "configured_nightly_import": self.settings.master_data_import_path is not None,
            "last_checked_at": (
                self._last_checked_at.isoformat() if self._last_checked_at is not None else None
            ),
            "last_reload_error": self._last_reload_error,
        }
        # The health endpoint reads this state instead of opening an encrypted
        # registry just for a simple info request.
        self._last_master_data_status = result
        return dict(result)

    def master_data_usable(self) -> bool:
        """Fail-closed when a configured first import has no last good state."""

        status = self.master_data_status()
        return bool(status["usable"])

    def _master_data_usable_for_status(self, status: RegistryStatus) -> bool:
        if self.settings.master_data_import_path is None:
            return True
        return self._last_reload_error is None or status.imported_at is not None

    def nlp_status(self) -> dict[str, Any]:
        analyzer = self._analyzer
        if analyzer is not None:
            return analyzer.nlp_status()
        # Before the first load, the device that the engine will use is assumed.
        self.device_selection()
        return self._pending_nlp_status()

    def hardware_capabilities(self) -> HardwareCapabilities:
        """CPU, RAM and GPU findings for this sidecar session."""

        with self._lock:
            if self._hardware_capabilities is None:
                self._hardware_capabilities = detect_hardware(
                    logical_cores=self.capacity.logical_cores,
                    memory_bytes=self.capacity.memory_bytes,
                    backend=self._hardware_backend,
                )
            return self._hardware_capabilities

    def device_selection(self) -> DeviceSelection:
        """One-time selection; health and NER read the same instance."""

        with self._lock:
            if self._device_selection is None:
                self._device_selection = select_device(
                    self.hardware_capabilities(),
                    requested=self.settings.presidio_device,
                    mps_fallback_env=self.settings.mps_fallback_enabled,
                    backend=self._hardware_backend,
                    diagnostics=self.diagnostics,
                )
            return self._device_selection

    def device_status(self) -> dict[str, Any]:
        return self.device_selection().as_dict()

    def worker_status(self) -> dict[str, Any]:
        """Actual pool sizes used instead of only automatically calculated ones."""

        status = self.capacity.as_dict()
        status["cpu_workers"] = self.settings.cpu_workers or self.capacity.cpu_workers
        # ``ocr_workers=0`` means serial, not disabled. The traces
        # have been attached to the engine since spec §7.1 (vision: two from 8 GB).
        status["ocr_concurrency"] = max(1, ocr_slots_for(selected_ocr_engine(), self.capacity))
        return status

    def _record_ner_fallback(self, error: Exception) -> None:
        """Record a later GPU model failure also in the health finding.

        Only when explicitly appropriate. A missing local model snapshot is not
        a hardware finding: the test inference on CUDA or MPS succeeded, only the
        preferred engine artifact is missing. Reporting both would create a
        GPU fallback that never happened and hide the real cause (spec §4, 5).
        """

        if isinstance(error, NerModelUnavailableError):
            self.diagnostics.record(
                "model_unavailable",
                "Das bevorzugte NER-Modell liegt lokal nicht vor; die Fallback-Engine übernimmt.",
                severity="warning",
                error_type=type(error).__name__,
                # The device probe remains valid: a file is missing, no GPU.
                device_unchanged=self.device_selection().active,
            )
            return

        with self._lock:
            selection = self.device_selection()
            self._device_selection = runtime_fallback(
                selection,
                reason="model_inference_failed",
                error_type=type(error).__name__,
                diagnostics=self.diagnostics,
            )

    def reload_configured(self) -> dict[str, Any]:
        sources = self.configured_sources()
        if sources is None:
            raise RegistryImportError("Kein Nightly-Stammdatenpfad konfiguriert.")
        self._last_checked_at = datetime.now(UTC)
        imported_signature = _sources_signature(sources)
        try:
            status = self.master_data().reimport(sources)
        except Exception as error:
            self._last_reload_error = _safe_reload_error(error)
            raise
        self._last_reload_error = None
        # The signature before import describes the read state. If the source is
        # replaced immediately after, the next poll will reliably detect the
        # deviation and re-import.
        self._source_signature = imported_signature
        return self.master_data_status() | status.as_dict()

    def start(self) -> None:
        # The device probe runs before the ready handshake. The UI never sees
        # an unchecked GPU as ready even briefly.
        #
        # The second probe with the GLiNER adapter does not belong here: it loads
        # the whole model, and while it ran there was no history or storage.
        # It now runs in ``start_warmup`` after the handshake; a fallback
        # corrects the health finding via the fallback callback, and an early
        # submitted document waits under the NER lock for the model.
        self.device_selection()
        # The local secret is created on first app start, not on first document.
        # An insecure or damaged secret blocks startup instead of later quietly
        # changing short codes.
        self.vault()
        self.profiles()
        try:
            self.purge_unused_mappings()
        except Exception as error:  # noqa: BLE001 — the hourly run retries
            self.diagnostics.record(
                "vault_janitor",
                "Ungenutzte Zuordnungen konnten nicht geloescht werden.",
                severity="warning",
                error_type=type(error).__name__,
            )
        self._start_entries_janitor()
        self._start_drafts_startup()
        if self.settings.master_data_import_path is None or self._watcher is not None:
            return
        try:
            self.reload_configured()
        except Exception as error:
            self._record_reload_failure(error)
        self._watcher = threading.Thread(
            target=self._watch_loop,
            name="pii-master-data-watch",
            daemon=True,
        )
        self._watcher.start()

    def start_warmup(self) -> None:
        """Build analyzer and NER model in background.

        Without this warm-up the first document pays the full load cost:
        decrypt registry, load model from disk. On a cold device this takes
        ten seconds or more – and previously it blocked the job that had just
        been submitted. This runs before the first document and does not block
        any request.

        Called by the running service after the handshake, not from
        ``start()``: the UI should be usable immediately, and a test for an API
        check must not load a language model.

        A failure is harmless: the lazy paths will be built on the first
        document instead.
        """

        if self._warmup is not None:
            return
        if setup_required():
            # Before initial setup there is nothing to warm up. The shell
            # restarts the sidecar once the models are installed.
            return

        def _warm() -> None:
            set_thread_qos(QosClass.UTILITY)
            try:
                # ``warm_up`` runs under the same lock as a real analysis; a
                # concurrently submitted document will not load the model a
                # second time.
                self.analyzer().warm_up()
            except Exception as error:  # noqa: BLE001 — warm-up is optional
                self.diagnostics.record(
                    "model_load",
                    "Die Vorwaermung der Erkennung ist fehlgeschlagen; sie laeuft "
                    "beim ersten Dokument erneut.",
                    severity="warning",
                    error_type=type(error).__name__,
                )

        self._warmup = threading.Thread(target=_warm, name="pii-warmup", daemon=True)
        self._warmup.start()
        self.start_ocr_warmup()

    def start_ocr_warmup(self) -> None:
        """Trigger Vision on macOS once (mini-image “unname”).

        The first call per helper and macOS build compiles Vision for the
        Neural Engine (≈ 50 s, measured in U0); afterwards a call costs
        0.2–0.3 s. It runs alongside model build because compilation mostly
        waits. If the OS build changed since the last start, this is recorded
        in diagnostics.
        """

        if self._ocr_warmup is not None or selected_ocr_engine() != APPLE_VISION:
            return

        def _warm() -> None:
            set_thread_qos(QosClass.UTILITY)
            from .extraction.ocr_vision import VisionOcrBackend, vision_helper_info, warm_up

            try:
                info = vision_helper_info()
            except OcrUnavailableError:
                return
            self._note_ocr_system(info.os_build, info.helper)
            backend = self.ocr_backend()
            try:
                self._ocr_warmup_ms = warm_up(
                    backend if isinstance(backend, VisionOcrBackend) else None
                )
            except OcrUnavailableError as error:
                self.diagnostics.record(
                    "ocr",
                    "Die Vorwaermung der Texterkennung ist fehlgeschlagen; sie laeuft "
                    "beim ersten Scan erneut.",
                    severity="warning",
                    error_type=type(error).__name__,
                )
                return
            self.diagnostics.record(
                "ocr",
                "Texterkennung vorgewaermt.",
                engine=APPLE_VISION,
                request="text",
                revision=3,
                os_version=info.os_version,
                os_build=info.os_build,
                helper=info.helper,
                duration_ms=self._ocr_warmup_ms,
            )

        self._ocr_warmup = threading.Thread(target=_warm, name="pii-warmup-ocr", daemon=True)
        self._ocr_warmup.start()

    def _note_ocr_system(self, os_build: str, helper: str) -> None:
        """Record OS build and helper; a change appears in diagnostics."""

        path = self.settings.state_directory / "ocr-engine.json"
        current = {"engine": APPLE_VISION, "os_build": os_build, "helper": helper}
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict) and previous == current:
            return
        if isinstance(previous, dict) and previous.get("os_build") != os_build:
            self.diagnostics.record(
                "ocr",
                "Texterkennung: neue Systemversion",
                previous_os_build=previous.get("os_build"),
                os_build=os_build,
            )
        try:
            _prepare_private_directory(self.settings.state_directory)
            path.write_text(json.dumps(current), encoding="utf-8")
        except OSError:
            # Diagnostic only: without the file the next start will not report the change.
            pass

    def _start_drafts_startup(self) -> None:
        """Reconcile draft state in background.

        Clean up, mark unreadable files as ``unavailable``, and resume any
        pending work with low priority. Continues after the handshake so the UI is
        responsive immediately.
        """

        if self._drafts_startup is not None:
            return
        from . import workflow

        def _run() -> None:
            set_thread_qos(QosClass.UTILITY)
            steps: tuple[tuple[str, Callable[[], object]], ...] = (
                ("sweep", self.sweep_drafts),
                ("reconcile", lambda: workflow.reconcile_drafts(self)),
            )
            if not setup_required():
                # Without models, resuming would only record errors;
                # the files wait until first open.
                steps = (*steps, ("resume", lambda: workflow.resume_interrupted(self)))
            for name, step in steps:
                if self._entries_janitor_stop.is_set():
                    return
                try:
                    step()
                except Exception as error:  # noqa: BLE001 — hourly run will retry
                    self.diagnostics.record(
                        "drafts_startup",
                        "Der Startabgleich des Arbeitsstands ist nicht vollstaendig gelaufen.",
                        severity="warning",
                        step=name,
                        error_type=type(error).__name__,
                    )

        self._drafts_startup = threading.Thread(
            target=_run, name="unname-drafts-startup", daemon=True
        )
        self._drafts_startup.start()

    def wait_for_startup(self, timeout: float = 30.0) -> None:
        """For tests and measurement: wait until reconciliation finishes."""

        thread = self._drafts_startup
        if thread is not None:
            thread.join(timeout)

    def _start_entries_janitor(self) -> None:
        if self._entries_janitor is not None:
            return

        def _loop() -> None:
            set_thread_qos(QosClass.UTILITY)
            # Hourly is enough: retention is in days, and each open
            # of the history also performs cleanup.
            while not self._entries_janitor_stop.wait(3600):
                try:
                    self.purge_expired_entries()
                except Exception as error:  # noqa: BLE001 — next run will retry
                    self.diagnostics.record(
                        "entries_janitor",
                        "Abgelaufene Verlaufseintraege konnten nicht geloescht werden.",
                        severity="warning",
                        error_type=type(error).__name__,
                    )
                try:
                    self.purge_unused_mappings()
                except Exception as error:  # noqa: BLE001 — next run will retry
                    self.diagnostics.record(
                        "vault_janitor",
                        "Ungenutzte Zuordnungen konnten nicht geloescht werden.",
                        severity="warning",
                        error_type=type(error).__name__,
                    )
                try:
                    self.sweep_drafts()
                except Exception as error:  # noqa: BLE001 — next run will retry
                    self.diagnostics.record(
                        "drafts_cleanup",
                        "Verwaiste Arbeitsstaende konnten nicht entfernt werden.",
                        severity="warning",
                        error_type=type(error).__name__,
                    )

        self._entries_janitor = threading.Thread(
            target=_loop,
            name="unname-entries-janitor",
            daemon=True,
        )
        self._entries_janitor.start()

    def close(self) -> None:
        self._entries_janitor_stop.set()
        self._watcher_stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=2)
        if self._warmup is not None:
            # A hanging model must not block shutdown;
            # the thread is a daemon and dies with the process.
            self._warmup.join(timeout=2)
            self._warmup = None
        if self._drafts_startup is not None:
            self._drafts_startup.join(timeout=2)
        self._ocr_warmup = None
        self._restored_files.clear()
        with self._lock:
            self._text_detections.clear()
            jobs = self._jobs
            self._jobs = None
            vault = self._vault
            self._vault = None
            self._processor = None
            self._extractor = None
            audit_log = self._audit_log
            self._audit_log = None
        if jobs is not None:
            jobs.close()
        if vault is not None:
            vault.close()
        if audit_log is not None:
            audit_log.close()

    def _watch_loop(self) -> None:
        while not self._watcher_stop.wait(self.settings.master_data_poll_seconds):
            try:
                sources = self.configured_sources()
                if sources is None:
                    return
                signature = _sources_signature(sources)
                self._last_checked_at = datetime.now(UTC)
                if signature != self._source_signature:
                    self.reload_configured()
            except Exception as error:
                safe_error = _safe_reload_error(error)
                if safe_error != self._last_reload_error:
                    self._record_reload_failure(error)

    def _record_reload_failure(self, error: Exception) -> None:
        self._last_reload_error = _safe_reload_error(error)
        self.diagnostics.record(
            "master_data_reload",
            "Der konfigurierte Stammdatenexport konnte nicht aktualisiert werden.",
            severity="warning",
            error_type=type(error).__name__,
        )


def _sources_signature(
    sources: RegistrySources,
) -> tuple[tuple[str, int, int, int, int, int] | None, ...]:
    signatures: list[tuple[str, int, int, int, int, int] | None] = []
    for path in dict.fromkeys((sources.persons, sources.organizations)):
        try:
            metadata = path.stat()
            signatures.append(
                (
                    str(path.resolve()),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
            )
        except FileNotFoundError:
            signatures.append(None)
    return tuple(signatures)


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def _safe_reload_error(error: Exception) -> str:
    # UI status shows the error class but never absolute source paths.
    return type(error).__name__


class _WithExtraRecognizers:
    """Master data, name lists and terms as level 0."""

    def __init__(self, registry: MasterDataRegistry, extra: tuple[Any, ...]) -> None:
        self._registry = registry
        self._extra = extra

    def recognizers(self, *, supported_language: str = "de") -> tuple[Any, ...]:
        return (*self._registry.recognizers(supported_language=supported_language), *self._extra)


def _spacy_tagger_reference(language: str) -> str | None:
    """Language package model for word classes; ``None`` when no package."""

    return spacy_model_reference(language) if spacy_available(language) else None
