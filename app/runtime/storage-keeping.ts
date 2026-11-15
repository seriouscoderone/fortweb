/**
 * Whether the browser has agreed to keep this wallet's storage.
 *
 * A browser may remove a site's storage when space runs short unless the site has asked to keep it and the browser
 * has agreed. Chrome and Edge decide by themselves, without asking the person, and say no to a site they have just
 * met; they may say yes once it has been used. So the wallet asks each time a vault is opened, which includes the
 * moment it is made. In a regular browser, Settings shows the answer. Inside the phone app the wallet asks too and
 * shows nothing: what Android's WebView answers, and what it then does, is not yet known.
 */
export type StorageKept = "kept" | "not-kept" | "unknown";

function storageManager(): StorageManager | null {
    return typeof navigator !== "undefined" && navigator.storage ? navigator.storage : null;
}

async function answer(ask: (storage: StorageManager) => Promise<boolean>): Promise<StorageKept> {
    const storage = storageManager();
    if (!storage) {
        return "unknown";
    }
    try {
        return (await ask(storage)) ? "kept" : "not-kept";
    } catch {
        return "unknown";
    }
}

/** Asks the browser to keep the wallet's storage. A browser that says no is asked again at the next opening. */
export function askToKeepStorage(): Promise<StorageKept> {
    return answer((storage) => (typeof storage.persist === "function" ? storage.persist() : Promise.reject()));
}

/** What the browser says now, without asking for anything. */
export function storageKept(): Promise<StorageKept> {
    return answer((storage) => (typeof storage.persisted === "function" ? storage.persisted() : Promise.reject()));
}
