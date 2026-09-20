/** The ApplicationV2 window. All logic lives in the injected `deps`. */
import { buildContext } from "./panel-state.mjs";

const { ApplicationV2, HandlebarsApplicationMixin } = foundry.applications.api;
const TEMPLATES = "modules/aigm-control-panel/templates";

// data-action -> controls method. Handlers run with `this` = the window.
const SIMPLE_ACTIONS = ["refreshStatus", "testConnection", "endSession", "pause", "resume", "startCombat", "stopCombat",
  "pan", "pushIn", "pullBack", "zoomIn", "zoomOut", "rollDice", "loadNpcs", "loadScenes"];
const simple = (name) => function (event, target) { return this.deps.controls[name](); };

export class AIGMControlPanel extends HandlebarsApplicationMixin(ApplicationV2) {
  static DEFAULT_OPTIONS = {
    id: "aigm-control-panel",
    classes: ["aigm-control-panel"],
    position: { width: 360, height: 640 },
    window: { title: "AIGM.title", icon: "fa-solid fa-robot", resizable: true },
    actions: {
      ...Object.fromEntries(SIMPLE_ACTIONS.map((name) => [name, simple(name)])),
      setMode(event, target) { return this.deps.controls.setMode(target.dataset.mode); },
      switchScene(event, target) { return this.deps.controls.switchToScene(target.dataset.scene); },
      narrate() { return this.#narrate(); },
    },
  };

  static PARTS = Object.fromEntries(
    ["header", "status", "controls", "spatial", "narration", "lists", "events"].map((part) => [part, { template: `${TEMPLATES}/${part}.hbs` }]),
  );

  /** @param {{state:object, controls:object, isOperator:()=>boolean, t:Function}} deps */
  constructor(deps, options = {}) {
    super(options);
    this.deps = deps;
  }

  async _prepareContext() {
    return buildContext(this.deps.state, { operator: this.deps.isOperator(), t: this.deps.t });
  }

  _onFirstRender() {
    this.deps.controls.loadSpatial();
  }

  _onRender(context, options) {
    // Ctrl/Cmd+Enter posts the narration. _onRender fires after every (partial)
    // render but the textarea survives them, so bind once per element.
    const textarea = this.element.querySelector('textarea[name="narration"]');
    if (!textarea || textarea.dataset.bound) return;
    textarea.dataset.bound = "1";
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        this.#narrate();
      }
    });
  }

  async #narrate() {
    const textarea = this.element.querySelector('textarea[name="narration"]');
    const includeSpatial = this.element.querySelector('input[name="includeSpatial"]')?.checked ?? false;
    if (await this.deps.controls.narrate(textarea?.value ?? "", includeSpatial)) textarea.value = "";
  }

  /** Re-render only the parts whose data changed. */
  refresh(parts) {
    if (this.rendered && parts.length) this.render({ parts });
  }
}
