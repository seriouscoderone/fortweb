// Per-vault fingerprint unlock on the Fortoid Android host.
//
// A vault's passcode can be saved in the phone's biometric-gated secret store
// (secret.* native capabilities). It is keyed by the vault's id AND creation
// time, so a different vault that later reuses the same id never matches.
// Without the native bridge (browser, iOS) everything reports "unavailable".

import { callNative, isNativeAvailable } from "./native-capabilities.js";

export interface FingerprintVault {
    id: string;
    alias: string;
    createdAt: string;
}

export interface FingerprintState {
    /** The phone can show a strong-biometric prompt. */
    available: boolean;
    /** This vault's passcode is saved for fingerprint unlock. */
    stored: boolean;
}

const UNAVAILABLE: FingerprintState = { available: false, stored: false };
const DECLINED_PREFIX = "fortweb.fingerprint.declined.";

export async function vaultSecretKey(vault: FingerprintVault): Promise<string> {
    const bytes = new TextEncoder().encode(`${vault.id}|${vault.createdAt}`);
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const hex = Array.from(digest.slice(0, 16), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `vault-${hex}`;
}

export async function fingerprintState(vault: FingerprintVault): Promise<FingerprintState> {
    if (!isNativeAvailable()) {
        return UNAVAILABLE;
    }
    try {
        const status = await callNative<{ stored: boolean; biometricAvailable: boolean }>(
            "secret.status",
            { key: await vaultSecretKey(vault) },
        );
        return { available: status.biometricAvailable, stored: status.stored };
    } catch {
        return UNAVAILABLE;
    }
}

export async function rememberPasscode(vault: FingerprintVault, passcode: string): Promise<void> {
    await callNative("secret.store", {
        key: await vaultSecretKey(vault),
        value: passcode,
        title: "Use fingerprint to unlock",
        subtitle: vault.alias,
    });
}

export async function recallPasscode(vault: FingerprintVault): Promise<string> {
    const { value } = await callNative<{ value: string }>("secret.retrieve", {
        key: await vaultSecretKey(vault),
        title: `Unlock ${vault.alias}`,
    });
    return value;
}

export async function forgetPasscode(vault: FingerprintVault): Promise<void> {
    await callNative("secret.delete", { key: await vaultSecretKey(vault) });
}

export async function hasDeclinedFingerprint(vault: FingerprintVault): Promise<boolean> {
    try {
        return window.localStorage.getItem(DECLINED_PREFIX + await vaultSecretKey(vault)) === "1";
    } catch {
        return false;
    }
}

export async function declineFingerprint(vault: FingerprintVault): Promise<void> {
    try {
        window.localStorage.setItem(DECLINED_PREFIX + await vaultSecretKey(vault), "1");
    } catch {
        // Storage unavailable: the offer may simply appear again next time.
    }
}
