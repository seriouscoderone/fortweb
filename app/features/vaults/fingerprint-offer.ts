import { declineFingerprint, rememberPasscode, type FingerprintVault } from "../../runtime/vault-fingerprint.js";
import { createModal } from "../../ui/composites/modal.js";
import { showToast } from "../../ui/composites/toast.js";

function errorCode(error: unknown): string {
    return typeof error === "object" && error !== null && "code" in error && typeof error.code === "string"
        ? error.code
        : "";
}

/** After a successful passcode unlock, offer to save the passcode for fingerprint unlock. */
export function offerFingerprint(vault: FingerprintVault, passcode: string): void {
    const modal = createModal({
        title: "Use fingerprint unlock?",
        body: `
            <p>Open <strong data-offer-vault></strong> with your fingerprint or face next time.</p>
            <p class="muted">Your passcode is encrypted by this phone's secure hardware and released only after a
            fingerprint or face match. You can turn this off from the unlock screen.</p>
        `,
        actions: [
            { label: "Not now", tone: "ghost", dataAction: "decline" },
            { label: "Use fingerprint", tone: "primary", dataAction: "accept" },
        ],
    });
    modal.open();

    const root = document.querySelector("[role='dialog'][aria-label='Use fingerprint unlock?']");
    if (!(root instanceof HTMLElement)) {
        return;
    }
    const vaultName = root.querySelector("[data-offer-vault]");
    if (vaultName) {
        vaultName.textContent = vault.alias;
    }

    root.querySelector("[data-action='decline']")?.addEventListener("click", () => {
        void declineFingerprint(vault);
        modal.close();
    });
    root.querySelector("[data-action='accept']")?.addEventListener("click", async () => {
        modal.close();
        try {
            await rememberPasscode(vault, passcode);
            showToast({ message: `Fingerprint unlock is on for ${vault.alias}.`, tone: "success" });
        } catch (error) {
            if (errorCode(error) !== "cancelled") {
                showToast({ message: "Fingerprint unlock was not turned on.", tone: "error" });
            }
        }
    });
}

