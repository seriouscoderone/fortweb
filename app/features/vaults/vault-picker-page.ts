export function renderVaultPickerPage() {
    return {
        title: "FortWeb",
        html: `
            <section class="home-splash" aria-labelledby="home-splash-title">
                <h1 class="visually-hidden" id="home-splash-title">FortWeb</h1>
                <img src="./assets/brand/SymbolLogo.svg" alt="" aria-hidden="true">
                <p class="visually-hidden">Open the vault drawer to initialize or choose a vault.</p>
            </section>
        `,
    };
}
/** Shown while the wallet runtime starts, before the vault list is known. */
export function renderStartingPage() {
    return {
        title: "FortWeb",
        html: `
            <section class="home-splash home-splash--starting" aria-labelledby="home-splash-title">
                <h1 class="visually-hidden" id="home-splash-title">FortWeb</h1>
                <img src="./assets/brand/SymbolLogo.svg" alt="" aria-hidden="true">
                <div class="loading-bar" role="progressbar" aria-label="Starting your wallet"></div>
                <p class="home-splash__status">Starting your wallet...</p>
            </section>
        `,
    };
}
