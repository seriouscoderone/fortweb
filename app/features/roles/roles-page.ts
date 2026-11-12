import { fieldTextHtml } from "../../ui/primitives/field-text.js";
import { announce } from "../../ui/core/a11y.js";
import { createModal } from "../../ui/composites/modal.js";

// Everything shown here comes from a source the wallet does not trust: it is set with textContent, .value or
// setAttribute only, never as markup or a link.

interface VaultRecord {
    id: string;
    alias: string;
}

interface DefinitionRef {
    said: string;
    alias: string;
    kind: string;
}

interface ReliedOnRef extends DefinitionRef {
    as: string;
}

interface Responsibility {
    said: string;
    alias: string;
    reliesOn: ReliedOnRef[];
    attests: DefinitionRef[];
    outcome: DefinitionRef | null;
}

interface RoleView {
    role: string;
    responsibilities: Responsibility[];
}

export interface PublicationView {
    target: string;
    name: string;
    description: string;
    tags: string[];
    collection: string;
    by: string | null;
    title: string;
    roles: RoleView[];
    problem: null | "mismatch" | "limit";
}

/** What `sources.read` answers. */
export interface SourceView {
    source: {
        address: string;
        host: string;
        by: string | null;
        oobi: string | null;
        introductions: number;
        catalogs: number;
    };
    checked: number;
    dropped: { entries: number; publications: number };
    publications: PublicationView[];
}

/** A role this vault has taken on, as `roles.list` and `roles.takeOn` answer. */
export interface TakenRole {
    id: string;
    address: string;
    host: string;
    target: string;
    publication: string;
    title: string;
    role: string;
    aid: string;
    identifierAlias: string;
    takenAt: string;
    responsibilities: Responsibility[];
    warnings: Array<{ code: string; message: string }>;
}

interface VaultIdentifier {
    aid: string;
    alias: string;
}

export type IdentifierChoice = { mode: "new"; alias: string } | { mode: "existing"; aid: string };

/** What `roles.takeOn` is asked, besides the vault. */
export interface TakeOnRequest {
    address: string;
    target: string;
    role: string;
    identifier: IdentifierChoice;
}

/** The last read of the open vault, kept by the app (in memory only) so that a re-render shows it again. */
export interface KeptRead {
    /** The address as typed. */
    address: string;
    view: SourceView | null;
    /** The sentence of a read that failed. */
    error: string;
    /** The publications (by index) whose roles are shown. */
    open: number[];
    /** Settles when the read finishes; null once it has. */
    pending: Promise<void> | null;
}

interface RolesPageProps {
    vault: VaultRecord;
    /** The roles this vault has taken on (`roles.list`), newest first. */
    roles: TakenRole[];
    identifiers: VaultIdentifier[];
    onReadSource(address: string): Promise<SourceView>;
    /** The kept last read, if any. */
    lastRead?(): KeptRead | null;
    onShowRoles?(index: number, open: boolean): void;
    /** Absent: the "Take on" buttons are disabled. Resolves once My roles shows the role. */
    onTakeOn?(request: TakeOnRequest): Promise<TakenRole>;
    /** Resolves once My roles no longer shows the role. */
    onRemove?(id: string): Promise<void>;
}

const NOT_APPOINTED_SENTENCE = "Nobody has granted this identifier the role. This wallet cannot hold grants yet.";

function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    text = "",
    className = "",
): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    if (text) {
        node.textContent = text;
    }
    if (className) {
        node.className = className;
    }
    return node;
}

function counted(count: number, one: string, many: string): string {
    return `${count} ${count === 1 ? one : many}`;
}

function errorMessage(error: unknown, fallback: string): string {
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string" && error.message) {
        return error.message;
    }
    return fallback;
}

/** The sentence shown for a read that failed. */
export function readErrorMessage(error: unknown): string {
    return errorMessage(error, "The address could not be read.");
}

/** A definition's alias, or the start of its SAID when it has none. */
function definitionName(ref: { alias: string; said: string }): string {
    return ref.alias || `${ref.said.slice(0, 8)}...`;
}

function responsibilityText(responsibility: Responsibility): string {
    const parts: string[] = [];
    if (responsibility.reliesOn.length > 0) {
        parts.push(`relies on ${responsibility.reliesOn
            .map((ref) => (ref.as ? `${definitionName(ref)} (as ${ref.as})` : definitionName(ref)))
            .join(", ")}`);
    }
    if (responsibility.attests.length > 0) {
        parts.push(`attests ${responsibility.attests.map(definitionName).join(", ")}`);
    }
    if (responsibility.outcome) {
        parts.push(`produces ${definitionName(responsibility.outcome)}`);
    }
    const name = definitionName(responsibility);
    return parts.length > 0 ? `${name}: ${parts.join("; ")}` : name;
}

function problemText(problem: PublicationView["problem"], host: string): string {
    return problem === "limit"
        ? `${host} publishes more than the wallet reads in one go.`
        : `A definition from ${host} does not match its identifier.`;
}

function sourceSummary(view: SourceView): HTMLElement {
    const { source } = view;
    const summary = element("section", "", "section-card roles-source");
    summary.dataset.rolesSource = "true";
    summary.append(element("h2", source.host));

    const counts = element("p",
        `${counted(view.publications.length, "publication", "publications")}, `
        + `${counted(view.checked, "definition checked", "definitions checked")}`);
    counts.dataset.rolesSummary = "true";
    summary.append(counts);

    if (source.by) {
        summary.append(element("p", `Unverified: says it is run by ${source.by}`, "muted roles-unverified"));
    }
    if (source.oobi) {
        summary.append(element("p", `Unverified: gives the OOBI ${source.oobi}`, "muted roles-unverified"));
    }
    if (source.introductions > 0 || source.catalogs > 0) {
        summary.append(element("p",
            `It introduces ${counted(source.introductions, "other source", "other sources")} and lists `
            + `${counted(source.catalogs, "catalog", "catalogs")}. The wallet does not follow them yet.`,
            "muted"));
    }
    const ignored = view.dropped.entries + view.dropped.publications;
    if (ignored > 0) {
        summary.append(element("p", `${counted(ignored, "entry was", "entries were")} ignored.`, "muted"));
    }
    return summary;
}

function dutiesList(responsibilities: Responsibility[], className: string): HTMLUListElement | null {
    if (responsibilities.length === 0) {
        return null;
    }
    const duties = element("ul", "", className);
    for (const responsibility of responsibilities) {
        const duty = element("li", responsibilityText(responsibility));
        duty.dataset.responsibility = "true";
        duties.append(duty);
    }
    return duties;
}

/** The role's name, numbered when an identifier of the vault already has it: cashier, cashier 2, cashier 3... */
function freeAlias(role: string, identifiers: VaultIdentifier[]): string {
    const taken = new Set(identifiers.map((identifier) => identifier.alias));
    let alias = role;
    for (let number = 2; taken.has(alias); number += 1) {
        alias = `${role} ${number}`;
    }
    return alias;
}

/** Focuses `selector` in the page shown now (after a re-render), if it is there. */
function focusShown(selector: string): void {
    const target = document.querySelector(selector);
    if (target instanceof HTMLElement) {
        target.focus();
    }
}

function openTakeOnDialog(
    request: { address: string; target: string; role: string },
    identifiers: VaultIdentifier[],
    onTakeOn: NonNullable<RolesPageProps["onTakeOn"]>,
): void {
    const modal = createModal({
        title: `Take on ${request.role}`,
        body: `
            <form class="roles-take-on" data-take-on-form novalidate>
                <fieldset class="roles-take-on__choices">
                    <legend class="roles-take-on__legend">Identifier</legend>
                    <label class="kf-radio-card roles-take-on__choice is-selected">
                        <input class="kf-radio-card__input" type="radio" name="take-on-mode" value="new" checked>
                        <span class="kf-radio-card__copy"><strong>Create a new identifier</strong></span>
                    </label>
                    <label class="kf-radio-card roles-take-on__choice" data-take-on-existing-choice>
                        <input class="kf-radio-card__input" type="radio" name="take-on-mode" value="existing">
                        <span class="kf-radio-card__copy"><strong>Select an existing identifier</strong></span>
                    </label>
                </fieldset>
                <div data-take-on-new>
                    ${fieldTextHtml({ id: "take-on-name", label: "Name" })}
                </div>
                <div class="field" data-take-on-existing hidden>
                    <label for="take-on-identifier">Identifier</label>
                    <select class="kf-select" id="take-on-identifier"></select>
                </div>
                <p class="status-line" data-take-on-status></p>
            </form>
        `,
        actions: [
            { label: "Cancel", tone: "ghost", dataAction: "cancel" },
            { label: "Take on", tone: "primary", dataAction: "submit" },
        ],
    });
    modal.open();

    const formNode = document.querySelector("[data-take-on-form]");
    const root = formNode?.closest("[role='dialog']");
    if (!(formNode instanceof HTMLFormElement) || !(root instanceof HTMLElement)) {
        throw new Error("Take on dialog is missing its form.");
    }
    const form = formNode;
    const name = form.querySelector("#take-on-name");
    const select = form.querySelector("#take-on-identifier");
    const newField = form.querySelector("[data-take-on-new]");
    const existingField = form.querySelector("[data-take-on-existing]");
    const existingChoice = form.querySelector("[data-take-on-existing-choice]");
    const statusNode = form.querySelector("[data-take-on-status]");
    const submitNode = root.querySelector("[data-action='submit']");
    const cancelNode = root.querySelector("[data-action='cancel']");
    if (!(name instanceof HTMLInputElement) || !(select instanceof HTMLSelectElement)
        || !(newField instanceof HTMLElement) || !(existingField instanceof HTMLElement)
        || !(existingChoice instanceof HTMLElement) || !(statusNode instanceof HTMLElement)
        || !(submitNode instanceof HTMLButtonElement) || !(cancelNode instanceof HTMLButtonElement)) {
        throw new Error("Take on dialog is missing a control.");
    }
    const status = statusNode;
    const submitButton = submitNode;
    const cancelButton = cancelNode;
    const nameInput = name;
    const identifierSelect = select;
    const newPanel = newField;
    const existingPanel = existingField;

    nameInput.value = freeAlias(request.role, identifiers);
    if (identifiers.length === 0) {
        existingChoice.remove();
    }
    for (const identifier of identifiers) {
        const option = element("option", identifier.alias);
        option.value = identifier.aid;
        identifierSelect.append(option);
    }

    const radios = Array.from(form.querySelectorAll<HTMLInputElement>("input[name='take-on-mode']"));
    function chosenMode(): "new" | "existing" {
        return radios.find((radio) => radio.checked)?.value === "existing" ? "existing" : "new";
    }
    function showChoice(): void {
        const mode = chosenMode();
        newPanel.hidden = mode !== "new";
        existingPanel.hidden = mode !== "existing";
        for (const radio of radios) {
            radio.closest(".kf-radio-card")?.classList.toggle("is-selected", radio.checked);
        }
    }
    for (const radio of radios) {
        radio.addEventListener("change", showChoice);
    }

    let working = false;
    async function submit(): Promise<void> {
        if (working) {
            return;
        }
        let identifier: IdentifierChoice;
        if (chosenMode() === "existing") {
            identifier = { mode: "existing", aid: identifierSelect.value };
        } else {
            const alias = nameInput.value.trim();
            if (!alias) {
                status.classList.remove("is-neutral");
                status.textContent = "Enter a name for the new identifier.";
                return;
            }
            identifier = { mode: "new", alias };
        }
        working = true;
        submitButton.disabled = true;
        cancelButton.disabled = true;
        status.classList.add("is-neutral");
        status.textContent = "Taking on the role...";
        try {
            const taken = await onTakeOn({ ...request, identifier });
            modal.close();
            focusShown(`[data-my-role="${CSS.escape(taken.id)}"] h3`);
        } catch (error) {
            const message = errorMessage(error, "The role could not be taken on.");
            status.classList.remove("is-neutral");
            status.textContent = message;
            announce(message, "assertive");
            submitButton.disabled = false;
            cancelButton.disabled = false;
        } finally {
            working = false;
        }
    }

    cancelButton.addEventListener("click", () => modal.close());
    submitButton.addEventListener("click", () => {
        void submit();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        void submit();
    });
}

/** What a result's buttons do. */
interface ResultHandlers {
    open: number[];
    onShowRoles: RolesPageProps["onShowRoles"];
    /** Absent: "Take on" is disabled. */
    takeOn?(publication: PublicationView, role: string): void;
}

function roleItem(
    publication: PublicationView,
    role: RoleView,
    headingId: string,
    handlers: ResultHandlers,
): HTMLLIElement {
    const item = element("li", "", "roles-role");
    const header = element("div", "", "roles-role__header");
    const heading = element("h5", role.role);
    heading.id = headingId;
    const takeOn = element("button", "Take on", "button button--secondary button--small");
    takeOn.type = "button";
    // Every button shows "Take on"; its name says which role. setAttribute keeps the role name text.
    takeOn.setAttribute("aria-label", `Take on ${role.role}`);
    const { takeOn: onTakeOn } = handlers;
    if (onTakeOn) {
        takeOn.addEventListener("click", () => onTakeOn(publication, role.role));
    } else {
        takeOn.disabled = true;
    }
    header.append(heading, takeOn);
    item.append(header);

    const duties = dutiesList(role.responsibilities, "roles-role__duties");
    if (duties) {
        item.append(duties);
    }
    return item;
}

function publicationCard(
    publication: PublicationView,
    index: number,
    host: string,
    handlers: ResultHandlers,
): HTMLElement {
    const prefix = `roles-publication-${index}`;
    const card = element("article", "", "section-card roles-publication");
    const name = element("h3", publication.name);
    name.id = `${prefix}-name`;
    card.setAttribute("aria-labelledby", name.id);
    card.append(name);
    if (publication.description) {
        card.append(element("p", publication.description));
    }
    if (publication.collection) {
        card.append(element("p", publication.collection, "muted roles-publication__collection"));
    }
    if (publication.tags.length > 0) {
        const tags = element("ul", "", "roles-publication__tags");
        tags.setAttribute("aria-label", "Tags");
        for (const tag of publication.tags) {
            tags.append(element("li", tag, "roles-tag"));
        }
        card.append(tags);
    }
    if (publication.by) {
        card.append(element("p", `Unverified: says it is published by ${publication.by}`, "muted roles-unverified"));
    }

    if (publication.problem) {
        card.append(element("p", problemText(publication.problem, host), "roles-publication__problem"));
        return card;
    }
    if (publication.roles.length === 0) {
        card.append(element("p", "It names no roles.", "muted"));
        return card;
    }

    const shown = handlers.open.includes(index);
    const toggle = element("button", shown ? "Hide roles" : "Show roles",
        "button button--ghost roles-publication__toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", shown ? "true" : "false");
    toggle.setAttribute("aria-controls", `${prefix}-roles`);

    const region = element("div", "", "roles-publication__roles");
    region.id = `${prefix}-roles`;
    region.hidden = !shown;
    region.append(element("h4", publication.title || "Roles"));
    const list = element("ul", "", "roles-role-list");
    list.dataset.rolesList = "true";
    publication.roles.forEach((role, roleIndex) => {
        list.append(roleItem(publication, role, `${prefix}-role-${roleIndex}`, handlers));
    });
    region.append(list);

    toggle.addEventListener("click", () => {
        const open = region.hidden;
        region.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.textContent = open ? "Hide roles" : "Show roles";
        handlers.onShowRoles?.(index, open);
    });

    card.append(toggle, region);
    return card;
}

function sourceResult(view: SourceView, handlers: ResultHandlers): DocumentFragment {
    const fragment = document.createDocumentFragment();
    fragment.append(sourceSummary(view));
    if (view.publications.length > 0) {
        const list = element("div", "", "roles-publications");
        view.publications.forEach((publication, index) => {
            list.append(publicationCard(publication, index, view.source.host, handlers));
        });
        fragment.append(list);
    }
    return fragment;
}

function takenRoleItem(role: TakenRole, onRemove: RolesPageProps["onRemove"], status: HTMLElement): HTMLLIElement {
    const item = element("li", "", "roles-mine__item");
    item.dataset.myRole = role.id;
    const heading = element("h3", role.role);
    heading.tabIndex = -1;
    item.append(
        heading,
        element("p", `${role.publication} at ${role.host}`, "muted"),
        element("p", `Identifier: ${role.identifierAlias || role.aid}`),
    );
    for (const warning of role.warnings) {
        const notice = element("div", "", "roles-warning");
        notice.append(element("p", warning.message, "roles-warning__title"));
        if (warning.code === "NOT_APPOINTED") {
            notice.append(element("p", NOT_APPOINTED_SENTENCE));
        }
        item.append(notice);
    }
    const duties = dutiesList(role.responsibilities, "roles-role__duties");
    if (duties) {
        item.append(duties);
    }

    const remove = element("button", "Remove", "button button--secondary button--small roles-mine__remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${role.role}`);
    if (onRemove) {
        remove.addEventListener("click", async () => {
            remove.disabled = true;
            status.textContent = "";
            try {
                await onRemove(role.id);
                focusShown("#roles-mine-heading");
            } catch (error) {
                if (!remove.isConnected) {
                    return;
                }
                const message = errorMessage(error, "The role could not be removed.");
                status.textContent = message;
                announce(message, "assertive");
                remove.disabled = false;
            }
        });
    } else {
        remove.disabled = true;
    }
    item.append(remove);
    return item;
}

function myRolesCard(roles: TakenRole[], onRemove: RolesPageProps["onRemove"]): HTMLElement {
    const card = element("section", "", "section-card roles-mine");
    const heading = element("h2", "My roles");
    heading.id = "roles-mine-heading";
    heading.tabIndex = -1;
    card.setAttribute("aria-labelledby", heading.id);
    card.append(heading);
    const status = element("p", "", "status-line");
    if (roles.length === 0) {
        card.append(element("p", "No roles yet. Open an address to see what you can take on.", "muted"));
    } else {
        const list = element("ul", "", "roles-mine__list");
        for (const role of roles) {
            list.append(takenRoleItem(role, onRemove, status));
        }
        card.append(list);
    }
    card.append(status);
    return card;
}

export function renderRolesPage({
    vault,
    roles,
    identifiers,
    onReadSource,
    lastRead,
    onShowRoles,
    onTakeOn,
    onRemove,
}: RolesPageProps) {
    return {
        title: "Roles",
        render(container: HTMLElement): void {
            container.replaceChildren();

            const page = element("section", "", "page-grid roles-page");

            const header = element("header", "", "page-header");
            const headingBlock = element("div");
            headingBlock.append(element("p", vault.alias, "page-header__eyebrow"), element("h1", "Roles"));
            header.append(headingBlock);

            const openCard = element("section", "", "section-card roles-open");
            openCard.setAttribute("aria-label", "Open an address");
            const form = element("form", "", "roles-open__form");
            form.dataset.rolesForm = "true";
            form.noValidate = true;
            form.innerHTML = `
                ${fieldTextHtml({ id: "roles-address", label: "Address", placeholder: "example.org" })}
                <button class="button button--primary" type="submit">Open</button>
            `;
            const status = element("p", "", "status-line");
            status.dataset.rolesStatus = "true";
            openCard.append(form, status);

            const result = element("div", "", "roles-result");
            result.dataset.rolesResult = "true";

            page.append(header, openCard, result, myRolesCard(roles, onRemove));
            container.append(page);
        },
        setup(root: HTMLElement): void {
            const formNode = root.querySelector("[data-roles-form]");
            const statusNode = root.querySelector("[data-roles-status]");
            const resultNode = root.querySelector("[data-roles-result]");
            const inputNode = root.querySelector("[data-roles-form] input");
            const openNode = root.querySelector("[data-roles-form] button[type='submit']");
            if (!(formNode instanceof HTMLFormElement) || !(statusNode instanceof HTMLElement)
                || !(resultNode instanceof HTMLElement) || !(inputNode instanceof HTMLInputElement)
                || !(openNode instanceof HTMLButtonElement)) {
                throw new Error("Roles page is missing its address form.");
            }
            const form = formNode;
            const status = statusNode;
            const result = resultNode;
            const input = inputNode;
            const open = openNode;
            input.setAttribute("autocapitalize", "none");
            input.setAttribute("spellcheck", "false");
            input.inputMode = "url";

            let reading = false;

            function showView(view: SourceView, shown: number[]): void {
                const take = onTakeOn;
                result.replaceChildren(sourceResult(view, {
                    open: shown,
                    onShowRoles,
                    takeOn: take
                        ? (publication, role) => openTakeOnDialog(
                            { address: view.source.address, target: publication.target, role }, identifiers, take)
                        : undefined,
                }));
            }

            function showError(message: string): void {
                status.classList.remove("is-neutral");
                status.textContent = message;
            }

            function startReading(): void {
                reading = true;
                open.disabled = true;
                result.replaceChildren();
                status.classList.add("is-neutral");
                status.textContent = "Reading...";
            }

            function stopReading(): void {
                reading = false;
                open.disabled = false;
                status.textContent = "";
            }

            function announceView(view: SourceView): void {
                announce(`${view.source.host}: `
                    + `${counted(view.publications.length, "publication", "publications")}, `
                    + `${counted(view.checked, "definition checked", "definitions checked")}`);
            }

            // Nothing is written into this page once it is gone (another page, or a re-render, replaced it).
            const shownNow = (): boolean => result.isConnected;

            async function read(): Promise<void> {
                if (reading) {
                    return;
                }
                startReading();
                announce("Reading...");
                try {
                    const view = await onReadSource(input.value.trim());
                    if (!shownNow()) {
                        return;
                    }
                    stopReading();
                    showView(view, []);
                    announceView(view);
                } catch (error) {
                    if (!shownNow()) {
                        return;
                    }
                    stopReading();
                    const message = readErrorMessage(error);
                    showError(message);
                    announce(message, "assertive");
                }
            }

            // The kept last read, as it was left. A read still running is shown when it finishes.
            const kept = lastRead?.() ?? null;
            if (kept) {
                input.value = kept.address;
                if (kept.view) {
                    showView(kept.view, kept.open);
                } else if (kept.error) {
                    showError(kept.error);
                }
                if (kept.pending) {
                    startReading();
                    void kept.pending.then(() => {
                        if (!shownNow()) {
                            return;
                        }
                        stopReading();
                        const finished = lastRead?.() ?? null;
                        if (finished?.view) {
                            showView(finished.view, finished.open);
                            announceView(finished.view);
                        } else if (finished?.error) {
                            showError(finished.error);
                            announce(finished.error, "assertive");
                        }
                    });
                }
            }

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                void read();
            });
        },
    };
}
