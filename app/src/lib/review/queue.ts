/**
 * The apply queue for a file, extracted from the old processing page.
 *
 * Only one call runs at a time; changes made meanwhile are collapsed to the
 * latest state. This keeps the sidecar order aligned with the visible decisions,
 * and a delayed response never overwrites a newer one. An apply is local and cheap —
 * the view shows the sidecar response after each change, not a local recompute.
 */
export interface Callbacks<A, E> {
	result: (result: E, job: A) => void;
	error: (error: unknown, job: A) => void;
	/** Is a call currently running or waiting? */
	running?: (running: boolean) => void;
}

export class ApplyQueue<A, E> {
	#ausstehend: { generation: number; job: A } | null = null;
	#active = false;
	#generation = 0;
	#waiters: (() => void)[] = [];
	readonly #run: (job: A) => Promise<E>;
	readonly #callbacks: Callbacks<A, E>;

	constructor(run: (job: A) => Promise<E>, callbacks: Callbacks<A, E>) {
		this.#run = run;
		this.#callbacks = callbacks;
	}

	get running(): boolean {
		return this.#active || this.#ausstehend !== null;
	}

	/** Compute a new state; replace any pending entry. */
	places(job: A): void {
		this.#ausstehend = { generation: ++this.#generation, job };
		this.#callbacks.running?.(true);
		void this.#clear();
	}

	/** Discard pending work and ignore ongoing responses. */
	discard(): void {
		this.#generation += 1;
		this.#ausstehend = null;
		if (!this.#active) this.#callbacks.running?.(false);
	}

	/** Waits until the latest queued decision has reached the Sidecar. */
	flush(): Promise<void> {
		if (!this.running) return Promise.resolve();
		return new Promise((resolve) => this.#waiters.push(resolve));
	}

	async #clear(): Promise<void> {
		if (this.#active) return;
		this.#active = true;
		try {
			while (this.#ausstehend) {
				const { generation, job } = this.#ausstehend;
				this.#ausstehend = null;
				try {
					const result = await this.#run(job);
					if (generation === this.#generation) this.#callbacks.result(result, job);
				} catch (error) {
					if (generation === this.#generation) this.#callbacks.error(error, job);
				}
			}
		} finally {
			this.#active = false;
			if (this.#ausstehend) void this.#clear();
			else {
				this.#callbacks.running?.(false);
				for (const resolve of this.#waiters.splice(0)) resolve();
			}
		}
	}
}
