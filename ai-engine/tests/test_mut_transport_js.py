"""Executes the playwright transport's injected combat hook in node: only turn/round changes may emit."""
import json
import shutil
import subprocess

import pytest

from foundry.playwright_transport import _INSTALL_CHANNEL

DRIVER = """
const install = (__JS__);
const emitted = [], hooks = {};
globalThis.window = {__aigmEmit: (m) => emitted.push(JSON.parse(m))};
globalThis.Hooks = {on: (name, fn) => { hooks[name] = fn; }};
install("combat-events");
const c = {id: "enc1", round: 2, turn: 0, started: true, turns: []};
const out = {};
for (const changed of [{hp: 1}, {turn: 1}, {round: 3}, {turn: 1, started: true}]) {
  emitted.length = 0;
  hooks.updateCombat(c, changed);
  out[JSON.stringify(changed)] = emitted.length;
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_update_combat_hook_emits_only_for_turn_or_round_changes():
    r = subprocess.run(["node", "-e", DRIVER.replace("__JS__", _INSTALL_CHANNEL)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {'{"hp":1}': 0, '{"turn":1}': 1, '{"round":3}': 1, '{"turn":1,"started":true}': 0}
