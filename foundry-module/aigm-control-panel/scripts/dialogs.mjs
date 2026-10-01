/** Foundry dialogs (DialogV2). Kept apart so the controls stay testable. */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function createDialogs({ t, speakers = () => [] }) {
  const { DialogV2 } = foundry.applications.api;

  return {
    /** @returns {Promise<{formula:string,speaker:string,flavor:string}|null>} */
    async askRoll() {
      const options = ["GM", ...speakers()].map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
      const input = await DialogV2.input({
        window: { title: t("AIGM.roll.title"), icon: "fa-solid fa-dice" },
        content: `
          <div class="form-group"><label>${t("AIGM.roll.formula")}</label><input type="text" name="formula" value="1d20" placeholder="2d6+3" autofocus></div>
          <div class="form-group"><label>${t("AIGM.roll.speaker")}</label><select name="speaker">${options}</select></div>
          <div class="form-group"><label>${t("AIGM.roll.flavor")}</label><input type="text" name="flavor" placeholder="${esc(t("AIGM.roll.flavorHint"))}"></div>`,
        ok: { label: t("AIGM.roll.ok"), icon: "fa-solid fa-dice" },
        rejectClose: false,
      });
      return input?.formula?.trim() ? input : null;
    },

    /** @returns {Promise<object|null>} the request body for /api/backstory, or null when cancelled */
    async askBackstory(fields) {
      const row = (name, label, value = "") => `<div class="form-group"><label>${t(label)}</label><input type="text" name="${name}" value="${esc(value)}"></div>`;
      const input = await DialogV2.input({
        window: { title: t("AIGM.backstory.title"), icon: "fa-solid fa-book-open" },
        content: [
          row("name", "AIGM.backstory.name", fields.name),
          row("ancestry", "AIGM.backstory.ancestry", fields.ancestry),
          row("char_class", "AIGM.backstory.class", fields.char_class),
          row("gender", "AIGM.backstory.gender"),
          row("age", "AIGM.backstory.age"),
          row("homeland", "AIGM.backstory.homeland"),
          row("tone", "AIGM.backstory.tone"),
          `<div class="form-group"><label>${t("AIGM.backstory.notes")}</label><textarea name="notes" rows="3" placeholder="${esc(t("AIGM.backstory.notesHint"))}"></textarea></div>`,
        ].join(""),
        ok: { label: t("AIGM.backstory.ok"), icon: "fa-solid fa-feather" },
        rejectClose: false,
      });
      return input?.name?.trim() ? input : null;
    },

    /** @returns {Promise<"save"|"close"|null>} */
    async showBackstory(text, { sources = [], canSave = false } = {}) {
      const paragraphs = esc(text).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
      const from = sources.length ? `<p class="hint">${esc(t("AIGM.backstory.sources", { list: sources.join(", ") }))}</p>` : "";
      const buttons = [
        ...(canSave ? [{ action: "save", label: t("AIGM.backstory.save"), icon: "fa-solid fa-floppy-disk", default: true }] : []),
        { action: "close", label: t("AIGM.backstory.close"), default: !canSave },
      ];
      return DialogV2.wait({
        window: { title: t("AIGM.backstory.result"), icon: "fa-solid fa-book-open" },
        content: `<div style="max-height:60vh;overflow:auto">${paragraphs}</div>${from}`,
        buttons,
        rejectClose: false,
      });
    },

    /** @returns {Promise<string|null>} the reason, or null when cancelled */
    async askEndReason() {
      const input = await DialogV2.input({
        window: { title: t("AIGM.end.title"), icon: "fa-solid fa-right-from-bracket" },
        content: `<div class="form-group"><label>${t("AIGM.end.reason")}</label><textarea name="reason" rows="3">${esc(t("AIGM.end.default"))}</textarea></div>`,
        ok: { label: t("AIGM.end.ok"), icon: "fa-solid fa-right-from-bracket" },
        rejectClose: false,
      });
      return input ? input.reason ?? "" : null;
    },
  };
}
