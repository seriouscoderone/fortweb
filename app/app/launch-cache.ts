// Remembers which vault is the default so the next launch can show its unlock
// screen (and fingerprint prompt) before the Pyodide runtime has booted.
// Only non-secret display fields are stored.

const LAUNCH_CACHE_KEY = "fortweb.launch.defaultVault";

export interface CachedVault {
    id: string;
    alias: string;
    createdAt: string;
    storageName?: string;
    otpConfigured?: boolean;
}

export function cachedDefaultVault(): CachedVault | null {
    try {
        const raw = window.localStorage.getItem(LAUNCH_CACHE_KEY);
        if (!raw) return null;
        const value = JSON.parse(raw) as Partial<CachedVault>;
        if (typeof value.id !== "string" || typeof value.alias !== "string" || typeof value.createdAt !== "string") {
            return null;
        }
        return {
            id: value.id,
            alias: value.alias,
            createdAt: value.createdAt,
            storageName: typeof value.storageName === "string" ? value.storageName : undefined,
            otpConfigured: value.otpConfigured === true,
        };
    } catch {
        return null;
    }
}

export function rememberDefaultVault(vault: CachedVault | null): void {
    try {
        if (!vault) {
            window.localStorage.removeItem(LAUNCH_CACHE_KEY);
            return;
        }
        const { id, alias, createdAt, storageName, otpConfigured } = vault;
        window.localStorage.setItem(LAUNCH_CACHE_KEY, JSON.stringify({ id, alias, createdAt, storageName, otpConfigured }));
    } catch {
        // Storage unavailable: launch simply waits for the runtime as before.
    }
}
