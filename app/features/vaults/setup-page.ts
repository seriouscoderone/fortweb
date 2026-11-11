import { floatingInputHtml, setupFloatingInputs } from "../../shared/components.js";
import { announce } from "../../ui/core/a11y.js";

export const MIN_PASSCODE_LENGTH = 8;
const DEFAULT_WALLET_NAME = "My Wallet";

interface SetupPageProps {
    /** Creates the wallet's (default) vault and opens it. */
    onCreateWallet(name: string, passcode: string): Promise<void>;
}

function errorMessage(error: unknown): string {
    return error instanceof Error && error.message ? error.message : "Wallet setup failed.";
}

/** First-run screen: one passcode and you're in. The vault concept stays out of sight. */
export function renderSetupPage({ onCreateWallet }: SetupPageProps) {
    return {
        title: "Set up your wallet",
        html: `
            <section class="unlock-stage">
                <div class="unlock-stage__splash" aria-hidden="true">
                    <img src="./assets/brand/SymbolLogo.svg" alt="">
                </div>
                <section class="unlock-dialog-card" aria-labelledby="setup-title">
                    <header class="unlock-dialog-card__header">
                        <div class="unlock-dialog-card__title-block">
                            <h1 id="setup-title">Set up your wallet</h1>
                            <p class="unlock-dialog-card__meta">Choose a passcode. You'll use it to open your wallet.</p>
                        </div>
                    </header>
                    <div class="lk-dialog__divider"></div>
                    <div class="unlock-dialog-card__body">
                        <form class="field-stack" data-setup-form novalidate>
                            ${floatingInputHtml({ label: "Passcode", name: "passcode", password: true })}
                            ${floatingInputHtml({ label: "Confirm passcode", name: "confirm", password: true })}
                            ${floatingInputHtml({ label: "Wallet name", name: "name" })}
                            <p class="status-line" data-setup-status role="status" aria-live="polite"></p>
                            <div class="lk-dialog__buttons unlock-dialog-card__buttons">
                                <button class="button button--primary" type="submit">Create wallet</button>
                            </div>
                        </form>
                    </div>
                </section>
            </section>
        `,
        setup(root: HTMLElement): void {
            const form = root.querySelector("[data-setup-form]");
            const status = root.querySelector("[data-setup-status]");
            if (!(form instanceof HTMLFormElement) || !(status instanceof HTMLElement)) {
                return;
            }
            const submit = form.querySelector("button[type='submit']");
            const nameInput = form.querySelector("input[name='name']");
            const passcodeInput = form.querySelector("input[name='passcode']");
            if (
                !(submit instanceof HTMLButtonElement) ||
                !(nameInput instanceof HTMLInputElement) ||
                !(passcodeInput instanceof HTMLInputElement)
            ) {
                return;
            }

            setupFloatingInputs(form);
            nameInput.value = DEFAULT_WALLET_NAME;

            function fail(message: string): void {
                status.classList.remove("status-line--loading");
                status.classList.add("status-line--error");
                status.textContent = message;
                announce(message, "assertive");
            }

            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                const data = new FormData(form);
                const passcode = String(data.get("passcode") || "");
                const confirm = String(data.get("confirm") || "");
                const name = String(data.get("name") || "").trim() || DEFAULT_WALLET_NAME;

                if (passcode.trim().length < MIN_PASSCODE_LENGTH) {
                    fail(`Choose a passcode of at least ${MIN_PASSCODE_LENGTH} characters.`);
                    passcodeInput.focus();
                    return;
                }
                if (passcode !== confirm) {
                    fail("The passcodes don't match.");
                    return;
                }

                submit.disabled = true;
                status.classList.remove("status-line--error");
                status.classList.add("status-line--loading");
                status.textContent = "Creating your wallet...";
                announce("Creating your wallet, please wait.");

                try {
                    await onCreateWallet(name, passcode);
                } catch (error) {
                    submit.disabled = false;
                    fail(errorMessage(error));
                }
            });
        },
    };
}
