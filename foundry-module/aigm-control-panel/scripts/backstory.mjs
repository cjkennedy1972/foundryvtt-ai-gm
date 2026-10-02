/** Actor-side helpers for the backstory feature. Pure: no Foundry globals, so they run under `node --test`. */

/** Where a system keeps the biography HTML. The first path that holds a string wins (dnd5e, then others). */
const BIOGRAPHY_PATHS = ["system.details.biography.value", "system.biography.value", "system.details.biography", "system.biography"];

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
const names = (items) => (items ?? []).map((i) => i.name).filter(Boolean).join(" / ");

/** Form defaults read off the sheet. Every field stays editable in the dialog. */
export function characterFields(actor) {
  return {
    name: actor.name ?? "",
    ancestry: names(actor.itemTypes?.race),
    char_class: names(actor.itemTypes?.class),
  };
}

export function biographyPath(actor) {
  return BIOGRAPHY_PATHS.find((p) => typeof get(actor, p) === "string") ?? null;
}

/** Appends under a dated rule, keeping what the player already wrote. */
export function appendBackstory(current, text, dateLabel) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const body = esc(text).trim().split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
  return `${current ?? ""}<hr><p><em>${esc(dateLabel)}</em></p>${body}`;
}
