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
