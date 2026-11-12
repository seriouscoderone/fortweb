import { fieldTextHtml } from "../../ui/primitives/field-text.js";
import { announce } from "../../ui/core/a11y.js";

// Everything shown here comes from a source the wallet does not trust: it is set with textContent only, never as
// markup or a link.

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

interface RolesPageProps {
    vault: VaultRecord;
    /** The roles this vault has taken on (`roles.list`). */
    roles: unknown[];
    identifiers: unknown[];
    onReadSource(address: string): Promise<SourceView>;
    /** Absent until taking on a role is wired: the "Take on" buttons are then disabled. */
    onTakeOn?(publication: PublicationView, role: string): Promise<void>;
    onRemove?(id: string): Promise<void>;
}

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

function errorMessage(error: unknown): string {
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string" && error.message) {
        return error.message;
    }
    return "The address could not be read.";
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

function roleItem(
    publication: PublicationView,
    role: RoleView,
    headingId: string,
    onTakeOn: RolesPageProps["onTakeOn"],
): HTMLLIElement {
    const item = element("li", "", "roles-role");
    const header = element("div", "", "roles-role__header");
    const heading = element("h5", role.role);
    heading.id = headingId;
    const takeOn = element("button", "Take on", "button button--secondary button--small");
    takeOn.type = "button";
    // Every button shows "Take on"; its name says which role. setAttribute keeps the role name text.
    takeOn.setAttribute("aria-label", `Take on ${role.role}`);
    if (typeof onTakeOn === "function") {
        takeOn.addEventListener("click", () => {
            void onTakeOn(publication, role.role);
        });
    } else {
        takeOn.disabled = true;
    }
    header.append(heading, takeOn);
    item.append(header);

    if (role.responsibilities.length > 0) {
        const duties = element("ul", "", "roles-role__duties");
        for (const responsibility of role.responsibilities) {
            const duty = element("li", responsibilityText(responsibility));
            duty.dataset.responsibility = "true";
            duties.append(duty);
        }
        item.append(duties);
    }
    return item;
}

function publicationCard(
    publication: PublicationView,
    index: number,
    host: string,
    onTakeOn: RolesPageProps["onTakeOn"],
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

    const toggle = element("button", "Show roles", "button button--ghost roles-publication__toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", `${prefix}-roles`);

    const region = element("div", "", "roles-publication__roles");
    region.id = `${prefix}-roles`;
    region.hidden = true;
    region.append(element("h4", publication.title || "Roles"));
    const list = element("ul", "", "roles-role-list");
    list.dataset.rolesList = "true";
    publication.roles.forEach((role, roleIndex) => {
        list.append(roleItem(publication, role, `${prefix}-role-${roleIndex}`, onTakeOn));
    });
    region.append(list);

    toggle.addEventListener("click", () => {
        const open = region.hidden;
        region.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.textContent = open ? "Hide roles" : "Show roles";
    });

    card.append(toggle, region);
    return card;
}

function sourceResult(view: SourceView, onTakeOn: RolesPageProps["onTakeOn"]): DocumentFragment {
    const fragment = document.createDocumentFragment();
    fragment.append(sourceSummary(view));
    if (view.publications.length > 0) {
        const list = element("div", "", "roles-publications");
        view.publications.forEach((publication, index) => {
            list.append(publicationCard(publication, index, view.source.host, onTakeOn));
        });
        fragment.append(list);
    }
    return fragment;
}

function myRolesCard(): HTMLElement {
    const card = element("section", "", "section-card roles-mine");
    const heading = element("h2", "My roles");
    heading.id = "roles-mine-heading";
    card.setAttribute("aria-labelledby", heading.id);
    card.append(heading, element("p", "No roles yet. Open an address to see what you can take on.", "muted"));
    return card;
}

export function renderRolesPage({ vault, onReadSource, onTakeOn }: RolesPageProps) {
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

            page.append(header, openCard, result, myRolesCard());
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

            async function read(): Promise<void> {
                if (reading) {
                    return;
                }
                reading = true;
                open.disabled = true;
                result.replaceChildren();
                status.classList.add("is-neutral");
                status.textContent = "Reading...";
                announce("Reading...");
                try {
                    const view = await onReadSource(input.value.trim());
                    status.textContent = "";
                    result.replaceChildren(sourceResult(view, onTakeOn));
                    announce(`${view.source.host}: `
                        + `${counted(view.publications.length, "publication", "publications")}, `
                        + `${counted(view.checked, "definition checked", "definitions checked")}`);
                } catch (error) {
                    const message = errorMessage(error);
                    status.classList.remove("is-neutral");
                    status.textContent = message;
                    announce(message, "assertive");
                } finally {
                    reading = false;
                    open.disabled = false;
                }
            }

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                void read();
            });
        },
    };
}
