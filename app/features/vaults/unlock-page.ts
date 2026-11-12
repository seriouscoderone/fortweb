import { homeHref } from "../../app/router.js";
import { NativeCallError } from "../../runtime/native-capabilities.js";
import { offerFingerprint } from "./fingerprint-offer.js";
import {
    fingerprintState,
    forgetPasscode,
    hasDeclinedFingerprint,
    recallPasscode,
    type FingerprintState,
} from "../../runtime/vault-fingerprint.js";
import { syncPasswordToggle } from "../../shared/components.js";
import { formatDateLabel } from "../../shared/dom.js";
import { announce } from "../../ui/core/a11y.js";
import { runLoadingBars } from "../../ui/primitives/loading-bar.js";

interface VaultRecord {
    id: string;
    alias: string;
    storageName?: string;
    createdAt: string;
    otpConfigured?: boolean;
}

interface UnlockPageProps {
    vault: VaultRecord;
    onOpenVault(passcode: string): Promise<void>;
}

function errorCode(error: unknown): string {
    return typeof error === "object" && error !== null && "code" in error && typeof error.code === "string"
        ? error.code
        : "";
}

const INVALIDATED_MESSAGE =
    "A new fingerprint or face was added to this phone, so fingerprint unlock was turned off. Enter your passcode.";
const STALE_PASSCODE_MESSAGE =
    "Your saved passcode no longer opens this vault, so fingerprint unlock was turned off. Enter your passcode.";

function errorMessage(error: unknown): string {
    if (
        typeof error === "object" &&
        error !== null &&
        "code" in error &&
        error.code === "TIMEOUT"
    ) {
        return "The vault took too long to open. This can happen after the app has been in the background. Please try again.";
    }

    return error instanceof Error && error.message ? error.message : "Vault open failed.";
}

export function renderUnlockPage({ vault, onOpenVault }: UnlockPageProps) {
    return {
        title: `Unlock ${vault.alias}`,
        html: `
            <section class="unlock-stage">
                <div class="unlock-stage__splash" aria-hidden="true">
                    <img src="./assets/brand/SymbolLogo.svg" alt="">
                </div>
                <section class="unlock-dialog-card" aria-labelledby="unlock-title">
                    <header class="unlock-dialog-card__header">
                        <div class="unlock-dialog-card__title-block">
                            <h1 id="unlock-title" data-unlock-title></h1>
                            <p class="unlock-dialog-card__meta" data-unlock-meta></p>
                        </div>
                        <a class="unlock-dialog-card__close" href="${homeHref()}" aria-label="Back to Vaults">
                            <img src="./assets/icons/close.svg" alt="">
                        </a>
                    </header>
                    <div class="lk-dialog__divider"></div>
                    <div class="unlock-dialog-card__body">
                        <form class="field-stack" data-unlock-form>
                            <div class="unlock-passcode-field">
                                <label class="visually-hidden" for="unlock-passcode">Passcode</label>
                                <input
                                    class="unlock-passcode-field__input"
                                    type="password"
                                    name="passcode"
                                    id="unlock-passcode"
                                    placeholder="Passcode"
                                    autocomplete="off"
                                    aria-describedby="unlock-status"
                                >
                                <button type="button" class="unlock-passcode-field__toggle" data-unlock-toggle aria-label="Toggle password visibility" aria-pressed="false">
                                    <img src="./assets/icons/visibility.svg" alt="" width="22" height="22">
                                </button>
                            </div>
                            <p class="status-line" id="unlock-status" data-unlock-status role="status" aria-live="polite"></p>
                            <div class="loading-bar" role="progressbar" aria-label="Opening vault" data-unlock-progress hidden></div>
                            <div class="lk-dialog__buttons unlock-dialog-card__buttons">
                                <a class="button button--secondary" href="${homeHref()}">Cancel</a>
                                <button class="button button--primary" type="submit">Open</button>
                            </div>
                            <button type="button" class="button button--secondary" data-unlock-fingerprint hidden>Unlock with fingerprint</button>
                            <button type="button" class="button button--ghost button--small" data-unlock-forget hidden>Forget fingerprint</button>
                        </form>
                    </div>
                </section>
            </section>
        `,
        setup(root: HTMLElement): void {
            const title = root.querySelector("[data-unlock-title]");
            const meta = root.querySelector("[data-unlock-meta]");
            const form = root.querySelector("[data-unlock-form]");
            const statusLine = root.querySelector("[data-unlock-status]");
            const toggleButton = root.querySelector("[data-unlock-toggle]");

            if (
                !(title instanceof HTMLElement) ||
                !(meta instanceof HTMLElement) ||
                !(form instanceof HTMLFormElement) ||
                !(statusLine instanceof HTMLElement)
            ) {
                return;
            }

            const submitButton = form.querySelector("button[type='submit']");
            const passcodeInput = form.querySelector("input[name='passcode']");

            if (!(submitButton instanceof HTMLButtonElement) || !(passcodeInput instanceof HTMLInputElement)) {
                return;
            }

            title.textContent = `Open ${vault.alias}`;
            meta.textContent = [
                vault.storageName || "Browser-safe vault",
                `Created ${formatDateLabel(vault.createdAt)}`,
                `2FA ${vault.otpConfigured ? "configured" : "deferred"}`,
            ].join(" · ");

            if (toggleButton instanceof HTMLElement) {
                toggleButton.addEventListener("click", () => {
                    passcodeInput.type = passcodeInput.type === "password" ? "text" : "password";
                    syncPasswordToggle(toggleButton, passcodeInput);
                });
            }

            const openButton: HTMLButtonElement = submitButton;
            const passcodeField: HTMLInputElement = passcodeInput;
            const status: HTMLElement = statusLine;
            const progress = root.querySelector("[data-unlock-progress]");
            const fingerprintButton = root.querySelector("[data-unlock-fingerprint]");
            const forgetButton = root.querySelector("[data-unlock-forget]");
            let fingerprint: FingerprintState = { available: false, stored: false };

            function showFingerprintControls(visible: boolean): void {
                if (fingerprintButton instanceof HTMLButtonElement) fingerprintButton.hidden = !visible;
                if (forgetButton instanceof HTMLButtonElement) forgetButton.hidden = !visible;
            }

            function setBusy(message: string, showProgress = true): void {
                if (progress instanceof HTMLElement) progress.hidden = !showProgress;
                if (showProgress) runLoadingBars();
                openButton.disabled = true;
                if (fingerprintButton instanceof HTMLButtonElement) fingerprintButton.disabled = true;
                status.textContent = message;
                status.classList.remove("status-line--error");
                status.classList.add("status-line--loading");
            }

            function setIdle(message = "", isError = false): void {
                if (progress instanceof HTMLElement) progress.hidden = true;
                openButton.disabled = false;
                if (fingerprintButton instanceof HTMLButtonElement) fingerprintButton.disabled = false;
                status.classList.remove("status-line--loading");
                status.classList.toggle("status-line--error", isError);
                status.textContent = message;
                if (message) {
                    announce(message, isError ? "assertive" : "polite");
                }
                passcodeField.focus();
            }

            async function disableFingerprint(message: string): Promise<void> {
                fingerprint = { ...fingerprint, stored: false };
                showFingerprintControls(false);
                await forgetPasscode(vault).catch(() => {});
                setIdle(message, true);
            }

            async function openVault(passcode: string, source: "passcode" | "fingerprint"): Promise<void> {
                setBusy("Opening vault...");
                announce("Opening vault, please wait.");

                try {
                    await onOpenVault(passcode);
                } catch (error) {
                    if (source === "fingerprint" && errorCode(error) === "AUTH_FAILED") {
                        await disableFingerprint(STALE_PASSCODE_MESSAGE);
                        return;
                    }
                    setIdle(errorMessage(error), true);
                    return;
                }

                if (
                    source === "passcode" &&
                    fingerprint.available &&
                    !fingerprint.stored &&
                    !(await hasDeclinedFingerprint(vault))
                ) {
                    offerFingerprint(vault, passcode);
                }
            }

            async function unlockWithFingerprint(): Promise<void> {
                setBusy("Waiting for fingerprint...", false);
                let passcode: string;
                try {
                    passcode = await recallPasscode(vault);
                } catch (error) {
                    const code = error instanceof NativeCallError ? error.code : errorCode(error);
                    if (code === "invalidated") {
                        fingerprint = { ...fingerprint, stored: false };
                        showFingerprintControls(false);
                        setIdle(INVALIDATED_MESSAGE, true);
                    } else if (code === "cancelled") {
                        setIdle();
                    } else {
                        setIdle(errorMessage(error), true);
                    }
                    return;
                }
                await openVault(passcode, "fingerprint");
            }

            fingerprintButton?.addEventListener("click", () => {
                void unlockWithFingerprint();
            });
            forgetButton?.addEventListener("click", async () => {
                await forgetPasscode(vault).catch(() => {});
                fingerprint = { ...fingerprint, stored: false };
                showFingerprintControls(false);
                setIdle("Fingerprint unlock turned off for this vault.");
            });

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                const formData = new FormData(form);
                void openVault(String(formData.get("passcode") || ""), "passcode");
            });

            void fingerprintState(vault).then((state) => {
                fingerprint = state;
                if (state.available && state.stored) {
                    showFingerprintControls(true);
                    void unlockWithFingerprint();
                }
            });
        },
    };
}
