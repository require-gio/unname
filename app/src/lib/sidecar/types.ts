/**
 * Counterparts to the types in `src-tauri/src/sidecar.rs` and `diagnostics.rs`,
 * plus `/health` and `/diagnostics`. Types for other endpoints are generated
 * from the sidecar schema (`api.gen.ts`).
 */

export type SidecarStatus =
	| { state: 'starting' }
	| {
			state: 'ready';
			/** Only for TCP (Windows); `null` when using a Unix socket (macOS). */
			port: number | null;
			transport: 'unix' | 'tcp';
			pid: number;
			version: string;
	  }
	| { state: 'unreachable'; reason: string; restarting: boolean }
	| { state: 'stopped' };

export interface SidecarSnapshot {
	status: SidecarStatus;
	restarts: number;
	changed_at: string;
	/**
	 * Fail-Closed: these two fields come from the shell and are read-only in the UI
	 * (spec §2).
	 */
	can_upload: boolean;
	can_export: boolean;
	/**
	 * `true` while name detection or language are missing – then initial setup is pending. `null` until the sidecar reports it.
	 */
	setup_required?: boolean | null;
}

/**
 * Base URL for UI calls: the `unname-api` scheme of the shell.
 * No token is needed here; the shell injects it itself.
 * `generation` changes with each new sidecar process.
 */
export interface SidecarConnection {
	base_url: string;
	generation: number;
}

export interface HealthReport {
	status: string;
	service: string;
	version: string;
	protocol: number;
	uptime_s: number;
	python: string;
	platform: { system: string; machine: string };
	components: Record<string, string | null>;
	nlp_engine: {
		preferred: string;
		fallback: string;
		selected?: string;
		active: string | null;
		loaded: boolean | null;
		fallback_reason?: string | null;
		load_policy?: string;
		/**
		 * Device of the **active** engine. Separate from `device.active`:
		 * a GPU confirmed by a test inference stays confirmed even if detection
		 * falls back to the CPU due to a missing local GLiNER snapshot.
		 */
		device?: string;
	};
	master_data: MasterDataStatus;
	workers: {
		cpu_workers: number;
		ocr_workers: number;
		logical_cores: number;
		memory_bytes: number;
		memory_gib: number;
	};
	job_worker: { healthy: boolean; reason: string | null };
	device: DeviceStatus;
	/** which models are present (`ready`/`missing`) and their source. */
	models?: Record<string, { state: 'ready' | 'missing'; source: string }>;
	setup_required?: boolean;
	/** which OCR engine is active – content-agnostic. */
	ocr?: OcrStatus;
}

/**
 * OCR: Apple Vision on macOS,
 * otherwise Tesseract. `warming`: Vision compiles its models once after
 * installation or system update (≈ 50 s).
 */
export interface OcrStatus {
	engine: 'apple-vision' | 'tesseract';
	state: 'ready' | 'warming' | 'missing';
	request?: string;
	revision?: number;
	helper?: string | null;
	os_version?: string | null;
	os_build?: string | null;
	version?: string | null;
	languages?: string[];
	warmup_ms?: number | null;
}

export type DeviceProbe = 'not_run' | 'ok' | 'failed';

export interface GpuStatus {
	/** PyTorch device identifier, e.g. `cuda:0` or `mps`. */
	device: string;
	/** Local device name, if the driver provides it without a network call. */
	name: string | null;
	/** Dedicated video memory; `null` for MPS or unknown size. */
	vram_bytes: number | null;
	/** Result of a real test inference on this exact device. */
	probe: DeviceProbe;
	/** According to the runtime matrix, usable only after explicit opt-in. */
	opt_in_required: boolean;
}

export interface DeviceStatus {
	/** Desired value from `PRESIDIO_DEVICE`; defaults to `auto` if unset. */
	requested: string;
	active: string;
	/**
	 * `not_run` while no test inference has run. Without a successful
	 * probe no GPU is claimed (spec §4, silent cupy fallback).
	 */
	probe: DeviceProbe;
	mps_fallback_env: boolean;
	/** Detected devices; only `gpus[].probe` indicates a successful test inference. */
	available: string[];
	gpus: GpuStatus[];
	/** Machine-readable reason for the effective device selection. */
	selection_reason: string;
	/** Device that automatically fell back to CPU. */
	fallback_from: string | null;
	/** Public, content-free short explanation of the fallback. */
	fallback_reason: string | null;
}

export type DiagnosticSeverity = 'info' | 'warning' | 'error';

export interface DiagnosticEntry {
	timestamp: string;
	category: string;
	severity: DiagnosticSeverity;
	message: string;
	details: Record<string, unknown>;
	source: 'harness' | 'sidecar';
}

export interface DiagnosticsReport {
	entries: DiagnosticEntry[];
	count: number;
}

export interface MasterDataStatus {
	persons_count: number;
	organizations_count: number;
	total_count: number;
	updated_at: string | null;
	imported_at: string | null;
	usable: boolean;
	configured_nightly_import: boolean;
	last_checked_at: string | null;
	last_reload_error: string | null;
}

/** State where nothing is allowed. Initial and fallback state. */
export const LOCKED: SidecarSnapshot = {
	status: { state: 'starting' },
	restarts: 0,
	changed_at: '',
	can_upload: false,
	can_export: false,
	setup_required: null
};
