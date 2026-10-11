() => {
  const serializeUser = (u) => ({id: u.id, name: u.name, role: u.role, isGM: u.isGM, active: u.active,
    color: u.color ?? null, avatar: u.avatar ?? null, character: u.character ?? null});
  const serializeSound = (s) => ({id: s.id, name: s.name, path: s.path, playing: s.playing,
    volume: s.volume, repeat: s.repeat, fade: s.fade});
  const serializePlaylist = (p) => ({id: p.id, name: p.name, description: p.description || "",
    playing: p.playing, mode: p.mode, fade: p.fade, folder: p.folder?.id || null, sorting: p.sorting,
    sounds: (p.sounds?.map(serializeSound)) || []});
  const resolvePlaylist = (d) => {
    const {playlistId, playlistName} = d;
    if (!playlistId && !playlistName) throw new Error("playlistId or playlistName is required");
    const playlist = playlistId ? game.playlists?.get(playlistId)
      : game.playlists?.find(p => p.name.toLowerCase() === playlistName.toLowerCase());
    if (!playlist) throw new Error(`Playlist not found: ${playlistId || playlistName}`);
    return playlist;
  };
  const resolveSound = (playlist, d) => {
    const {soundId, soundName} = d;
    if (!soundId && !soundName) return null;
    const sound = soundId ? playlist.sounds.get(soundId)
      : playlist.sounds.find(s => s.name.toLowerCase() === soundName.toLowerCase());
    if (!sound) throw new Error(`Sound not found: ${soundId || soundName}`);
    return sound;
  };
  const sceneFor = (d) => (d.sceneId ? game.scenes?.get(d.sceneId) : game.scenes?.active) ?? null;
  const requireScene = (d) => {
    const scene = sceneFor(d);
    if (!scene) throw new Error(d.sceneId ? `Scene not found: ${d.sceneId}` : "No active scene");
    return scene;
  };
  // The engine passes a document uuid (Scene.<id>.Token.<id>); the module wants the scene and the document id.
  const sceneFromUuid = (uuid) => {
    const parts = uuid?.split(".") ?? [];
    return parts[0] === "Scene" ? game.scenes?.get(parts[1]) ?? null : null;
  };
  const idFromUuid = (uuid) => (uuid ? uuid.split(".").pop() : undefined);
  const CANVAS_COLLECTIONS = new Set(["tokens", "tiles", "drawings", "lights", "sounds", "notes",
    "templates", "walls", "regions"]);
  // Attribute changes, entity CRUD, and scene updates (module: entity.ts, scene.ts).
  const applyAttribute = async (d, sign) => {
    if (!d.uuid && !d.selected) throw new Error("UUID or selected is required");
    if (!d.attribute) throw new Error("Attribute path is required");
    if (typeof d.amount !== "number") throw new Error("Amount must be a number");
    if (d.selected) throw new Error("selected targeting is not ported to the playwright backend yet");
    const entity = await fromUuid(d.uuid);
    if (!entity) throw new Error("No entities found to modify");
    const current = foundry.utils.getProperty(entity, d.attribute);
    if (typeof current !== "number") throw new Error(`Attribute ${d.attribute} is not a number, found: ${typeof current}`);
    const next = current + sign * d.amount;
    await entity.update({[d.attribute]: next});
    return {results: [{uuid: entity.uuid, attribute: d.attribute, oldValue: current, newValue: next}], success: true};
  };
  // dnd5e group (module: dnd5e.ts, encounter.ts, effects.ts). Only the dnd5e 5+ branch is ported.
  const safeJson = (v) => { try { return JSON.parse(JSON.stringify(v)); } catch { return {completed: true}; } };
  const requireDnd5eV5 = () => {
    const major = parseInt(game.system.version.split(".")[0], 10);
    if (major < 5) throw new Error(`dnd5e ${game.system.version} is not ported to the playwright backend (needs 5+)`);
  };
  const restActor = async (d) => {
    if (d.actorUuid) {
      const actor = await fromUuid(d.actorUuid);
      if (!actor) throw new Error("Actor not found");
      return actor;
    }
    if (d.selected) {
      const token = canvas.tokens?.controlled?.[0];
      if (!token) throw new Error("No token selected");
      return token.actor;
    }
    throw new Error("Either actorUuid or selected must be provided");
  };
  const actorBy = async (d) => {
    if (!d.actorUuid && !d.actorName) throw new Error("actorUuid or actorName is required");
    const found = d.actorUuid ? await fromUuid(d.actorUuid)
      : game.actors?.find(a => a.name.toLowerCase() === d.actorName.toLowerCase());
    if (!found) throw new Error(`Actor not found: ${d.actorUuid || d.actorName}`);
    return found.actor ?? found;
  };
  const rest = async (d, kind) => {
    const actor = await restActor(d);
    const options = {dialog: false, chat: true};
    if (kind === "short") {
      if (d.autoHD !== undefined) options.autoHD = d.autoHD;
      if (d.autoHDThreshold !== undefined) options.autoHDThreshold = d.autoHDThreshold;
    } else {
      options.newDay = d.newDay !== undefined ? d.newDay : true;
    }
    const result = kind === "short" ? await actor.shortRest(options) : await actor.longRest(options);
    return {actorUuid: actor.uuid, result: result ? safeJson(result) : {completed: true}};
  };
  window.__aigmOps = {
    shortRest: (d) => rest(d, "short"),
    longRest: (d) => rest(d, "long"),
    abilitySave: async (d) => {
      requireDnd5eV5();
      const valid = ["str", "dex", "con", "int", "wis", "cha"];
      if (!d.ability) throw new Error("ability is required");
      if (!valid.includes(d.ability)) throw new Error(`Invalid ability '${d.ability}'. Valid abilities: ${valid.join(", ")}`);
      if (!d.actorUuid) throw new Error("actorUuid is required");
      const actor = await fromUuid(d.actorUuid);
      if (!actor) throw new Error(`Actor not found with UUID: ${d.actorUuid}`);
      const config = {ability: d.ability};
      if (d.advantage) config.advantage = true;
      if (d.disadvantage) config.disadvantage = true;
      if (d.bonus) config.rolls = [{parts: [d.bonus]}];
      const rolls = await actor.rollSavingThrow(config, {configure: false}, {create: d.createChatMessage === true});
      const roll = Array.isArray(rolls) ? rolls[0] : rolls;
      return {actorUuid: actor.uuid, ability: d.ability, total: roll?.total, formula: roll?.formula, result: roll?.result};
    },
    roll: async (d) => {
      if (!d.formula) throw new Error("formula is required");
      const roll = await new Roll(d.formula).evaluate();
      // createChatMessage makes it a real chat roll so dice addons animate it for every client.
      if (d.createChatMessage === true) {
        await roll.toMessage({speaker: {alias: d.speaker || ""}, flavor: d.flavor || ""});
      }
      return {formula: roll.formula, total: roll.total, result: roll.result, speaker: d.speaker || ""};
    },
    skillCheck: async (d) => {
      requireDnd5eV5();
      if (!d.actorUuid) throw new Error("actorUuid is required");
      if (!d.skill) throw new Error("skill is required");
      const actor = await fromUuid(d.actorUuid);
      if (!actor) throw new Error(`Actor not found with UUID: ${d.actorUuid}`);
      const config = {skill: d.skill};
      if (d.advantage) config.advantage = true;
      if (d.disadvantage) config.disadvantage = true;
      const rolls = await actor.rollSkill(config, {configure: false}, {create: d.createChatMessage === true});
      const roll = Array.isArray(rolls) ? rolls[0] : rolls;
      return {actorUuid: actor.uuid, skill: d.skill, total: roll?.total, formula: roll?.formula, result: roll?.result};
    },
    switchScene: async (d) => {
      const scene = d.sceneId ? game.scenes?.get(d.sceneId)
        : game.scenes?.find(s => s.name.toLowerCase() === (d.name || "").toLowerCase());
      if (!scene) throw new Error(`Scene not found: ${d.sceneId || d.name}`);
      await scene.activate();
      return {ok: true, sceneId: scene.id, name: scene.name};
    },
    createCanvasDocument: async (d) => {
      const scene = sceneFromUuid(d.uuid) ?? requireScene(d);
      const created = await scene.createEmbeddedDocuments(d.className, Array.isArray(d.data) ? d.data : [d.data]);
      return created.map(x => x.toObject());
    },
    deathSave: async (d) => {
      requireDnd5eV5();
      if (!d.actorUuid) throw new Error("actorUuid is required");
      const actor = await fromUuid(d.actorUuid);
      if (!actor) throw new Error(`Actor not found with UUID: ${d.actorUuid}`);
      const config = d.advantage ? {advantage: true} : {};
      const rolls = await actor.rollDeathSave(config, {configure: false}, {create: d.createChatMessage === true});
      const roll = Array.isArray(rolls) ? rolls[0] : rolls;
      const death = actor.system?.attributes?.death || {};
      return {actorUuid: actor.uuid, total: roll?.total, formula: roll?.formula, result: roll?.result,
        deathSaves: {success: death.success || 0, failure: death.failure || 0}};
    },
    breakConcentration: async (d) => {
      const actor = await actorBy(d);
      const effect = actor.effects?.find(e => e.statuses?.has("concentrating") || e.statuses?.has("concentration"));
      if (!effect) throw new Error(`Actor '${actor.name}' is not concentrating`);
      await actor.deleteEmbeddedDocuments("ActiveEffect", [effect.id]);
      return {actorUuid: actor.uuid, broken: true, removedEffectId: effect.id};
    },
    addEffect: async (d) => {
      if (!d.uuid) throw new Error("uuid is required");
      if (!d.statusId && !d.effectData) throw new Error("Either statusId or effectData is required");
      const document = await fromUuid(d.uuid);
      if (!document) throw new Error(`Document not found with UUID: ${d.uuid}`);
      const actor = document.actor || document;
      let created;
      if (d.statusId) {
        const statusEffect = CONFIG.statusEffects?.find(s => s.id === d.statusId);
        if (!statusEffect) {
          const available = (CONFIG.statusEffects || []).map(s => s.id).join(",");
          throw new Error(`Unknown status effect: '${d.statusId}'. Available: ${available}`);
        }
        if (actor.effects?.find(e => e.statuses?.has(d.statusId))) {
          throw new Error(`Actor '${actor.name}' already has status '${d.statusId}'`);
        }
        if (document.documentName === "Token" && document.object?.toggleActiveEffect) {
          await document.object.toggleActiveEffect(statusEffect, {active: true});
          created = actor.effects?.find(e => e.statuses?.has(d.statusId));
        } else {
          const payload = {name: statusEffect.name || statusEffect.label || d.statusId,
            icon: statusEffect.icon || statusEffect.img, statuses: [d.statusId]};
          [created] = await actor.createEmbeddedDocuments("ActiveEffect", [payload]);
        }
      } else {
        [created] = await actor.createEmbeddedDocuments("ActiveEffect", [d.effectData]);
      }
      return {uuid: actor.uuid, effect: created ? {id: created.id, uuid: created.uuid,
        name: created.name || created.label, icon: created.icon || created.img,
        statuses: Array.from(created.statuses || [])} : null};
    },
    startEncounter: async (d) => {
      const combat = await Combat.create({name: d.name || "New Encounter"});
      if (!combat) throw new Error("Failed to create encounter");
      await combat.startCombat();
      if (d.tokenUuids?.length) {
        const tokensData = [];
        for (const uuid of d.tokenUuids) {
          try {
            const token = await fromUuid(uuid);
            if (token) tokensData.push({tokenId: token.id ?? "", sceneId: token.parent.id});
          } catch (err) {
            console.warn("[aigm] failed to add token to combat", uuid, err);
          }
        }
        if (tokensData.length) await combat.createEmbeddedDocuments("Combatant", tokensData);
      }
      const addedTokenIds = new Set();
      if (d.startWithPlayers) {
        const scene = game.scenes?.viewed;
        if (scene) {
          const playerTokens = scene.tokens?.filter(t => !!t.actor && t.actor.hasPlayerOwner) ?? [];
          const data = playerTokens.map(t => { addedTokenIds.add(t.id); return {tokenId: t.id, sceneId: scene.id}; });
          if (data.length) await combat.createEmbeddedDocuments("Combatant", data);
        }
      }
      if (d.startWithSelected) {
        const selected = (canvas?.tokens?.controlled ?? [])
          .filter(t => !addedTokenIds.has(t.id))
          .map(t => ({tokenId: t.id, sceneId: t.scene.id}));
        if (selected.length) await combat.createEmbeddedDocuments("Combatant", selected);
      }
      if (d.rollNPC) await combat.rollNPC();
      if (d.rollAll) await combat.rollAll();
      await combat.activate();
      return {encounterId: combat.id, encounter: {id: combat.id, name: combat.name, round: combat.round,
        turn: combat.turn, combatants: combat.combatants.contents.map(c => ({id: c.id, name: c.name,
          tokenUuid: c.token?.uuid, actorUuid: c.actor?.uuid, img: c.img, initiative: c.initiative,
          hidden: c.hidden, defeated: c.isDefeated}))}};
    },
    endEncounter: async (d) => {
      const id = d.encounterId || game.combat?.id;
      const combat = game.combats?.get(id);
      if (!combat) throw new Error("No encounter not found");
      await combat.delete();
      return {encounterId: id, message: "Encounter successfully ended"};
    },
    decreaseAttribute: (d) => applyAttribute(d, -1),
    increaseAttribute: (d) => applyAttribute(d, 1),
    createEntity: async (d) => {
      if (d.entityType === "Macro") throw new Error("Macro writes are not ported to the playwright backend yet");
      const DocumentClass = CONFIG[d.entityType]?.documentClass;
      if (!DocumentClass) throw new Error(`Invalid entity type: ${d.entityType}`);
      const createData = {...d.data, folder: d.folder || null};
      const options = (d.keepId && createData._id) ? {keepId: true} : {};
      if (createData._id && game.collections.get(d.entityType)?.get(createData._id)) {
        throw new Error(`Entity with ID '${createData._id}' already exists. Set override=true to replace it.`);
      }
      const entity = await DocumentClass.create(createData, options);
      if (!entity) throw new Error("Failed to create entity");
      return {uuid: entity.uuid, entity: entity.toObject()};
    },
    updateEntity: async (d) => {
      if (!d.uuid) throw new Error("uuid is required; selected-token updates are not ported to the playwright backend yet");
      const entity = await fromUuid(d.uuid);
      if (!entity) throw new Error(`Entity not found: ${d.uuid}`);
      if (entity.documentName === "Macro") throw new Error("Macro writes are not ported to the playwright backend yet");
      await entity.update(d.data);
      const updated = await fromUuid(entity.uuid);
      return {uuid: d.uuid, entity: [updated?.toObject()]};
    },
    updateScene: async (d) => {
      const scene = d.name ? game.scenes?.find(s => s.name.toLowerCase() === d.name.toLowerCase()) : sceneFor(d);
      if (!scene) throw new Error("Scene not found");
      const sceneData = {...d.data};
      // v14 moved background and foreground onto the scene's first Level document.
      if ("background" in sceneData || "foreground" in sceneData) {
        const levelUpdate = {};
        if ("background" in sceneData) { levelUpdate.background = sceneData.background; delete sceneData.background; }
        if ("foreground" in sceneData) { levelUpdate.foreground = {src: sceneData.foreground}; delete sceneData.foreground; }
        if (scene.firstLevel) await scene.updateEmbeddedDocuments("Level", [{_id: scene.firstLevel.id, ...levelUpdate}]);
      }
      try {
        await scene.update(sceneData);
      } catch (err) {
        // A PixiJS error after a server-side write still counts as saved, as in the module.
        if (!game.scenes?.get(scene.id)) throw err;
        console.warn("[aigm] scene update had a rendering error but data was saved", err);
      }
      return (game.scenes?.get(scene.id) ?? scene).toObject();
    },
    getScene: (d) => {
      if (d.all) return (game.scenes?.contents ?? []).map(s => s.toObject(true));
      const scene = d.name ? game.scenes?.find(s => s.name.toLowerCase() === d.name.toLowerCase()) : sceneFor(d);
      if (!scene) throw new Error("Scene not found");
      return scene.toObject(true);
    },
    getCanvasDocuments: (d) => {
      const scene = requireScene(d);
      if (!CANVAS_COLLECTIONS.has(d.documentType)) throw new Error(`Invalid document type: ${d.documentType}`);
      const collection = scene[d.documentType];
      if (d.documentId) {
        const doc = collection.get(d.documentId);
        if (!doc) throw new Error(`Document not found: ${d.documentId}`);
        return [doc.toObject()];
      }
      return collection.contents.map(x => x.toObject());
    },
    updateCanvasDocument: async (d) => {
      const scene = sceneFromUuid(d.uuid) ?? requireScene(d);
      const id = d.documentId ?? idFromUuid(d.uuid);
      const updated = await scene.updateEmbeddedDocuments(d.className, [{_id: id, ...d.data}]);
      return updated.map(x => x.toObject());
    },
    deleteCanvasDocument: async (d) => {
      const scene = sceneFromUuid(d.uuid) ?? requireScene(d);
      const ids = d.ids ?? (Array.isArray(d.documentId) ? d.documentId : [d.documentId ?? idFromUuid(d.uuid)]);
      await scene.deleteEmbeddedDocuments(d.className, ids);
      return {success: true};
    },
    moveToken: async (d) => {
      const scene = sceneFromUuid(d.uuid) ?? requireScene(d);
      const {uuid, name, x, y, waypoints} = d;
      if (typeof x !== "number" || typeof y !== "number") throw new Error("x and y coordinates are required");
      if (!uuid && !name && !d.tokenId) throw new Error("uuid or name is required");
      let token = null;
      if (uuid) {
        const doc = await fromUuid(uuid);
        if (doc?.documentName === "Token") token = doc;
        else if (doc?.documentName === "Actor") token = scene.tokens?.find(t => t.actor?.id === doc.id);
      } else if (name) {
        token = scene.tokens?.find(t => t.name?.toLowerCase() === name.toLowerCase());
      } else {
        token = scene.tokens?.get(d.tokenId);
      }
      if (!token) throw new Error(`Token not found: ${uuid || name || d.tokenId}`);
      const animate = d.animate !== false;
      for (const wp of waypoints ?? []) {
        if (typeof wp.x !== "number" || typeof wp.y !== "number") throw new Error("Each waypoint must have x and y coordinates");
        await token.update({x: wp.x, y: wp.y}, {animate});
        if (animate) await new Promise(r => setTimeout(r, 300));
      }
      await token.update({x, y}, {animate});
      return {tokenUuid: token.uuid, name: token.name, x, y, sceneId: scene.id};
    },
    removeEffect: async (d) => {
      const document = await fromUuid(d.uuid);
      if (!document) throw new Error(`Document not found with UUID: ${d.uuid}`);
      const actor = document.actor || document;
      const effect = actor.effects?.find(e => e.statuses?.has(d.statusId));
      if (!effect) throw new Error(`Status '${d.statusId}' not found on ${actor.name}`);
      if (document.documentName === "Token" && document.object?.toggleActiveEffect) {
        const statusEffect = CONFIG.statusEffects?.find(s => s.id === d.statusId);
        await document.object.toggleActiveEffect(statusEffect, {active: false});
      } else {
        await actor.deleteEmbeddedDocuments("ActiveEffect", [effect.id]);
      }
      return {uuid: actor.uuid, statusId: d.statusId, removed: true};
    },
    uploadFile: async (d) => {
      // fileData is a data URL; Foundry's own FilePicker writes it into the data directory.
      const blob = await (await fetch(d.fileData)).blob();
      const file = new File([blob], d.filename, {type: d.mimeType});
      const FilePicker = foundry.applications.apps.FilePicker.implementation;
      try {
        await FilePicker.createDirectory(d.source, d.path, {});
      } catch (err) {
        // The folder already exists, which is the usual case; anything else surfaces on upload.
      }
      const result = await FilePicker.upload(d.source, d.path, file, {}, {notify: false});
      if (!result?.path) throw new Error(`upload to ${d.source}/${d.path} returned no path`);
      return {path: result.path, message: result.message ?? "", status: result.status ?? "success"};
    },
    getUsers: () => (game.users?.contents ?? []).map(serializeUser),
    getPlaylists: () => ({playlists: (game.playlists?.contents ?? []).map(serializePlaylist)}),
    playlistPlay: async (d) => {
      const playlist = resolvePlaylist(d);
      const sound = resolveSound(playlist, d);
      if (sound) await playlist.playSound(sound); else await playlist.playAll();
      return {playlist: serializePlaylist(playlist)};
    },
    playlistVolume: async (d) => {
      const playlist = resolvePlaylist(d);
      const sound = resolveSound(playlist, d);
      const volume = d.volume;
      if (typeof volume !== "number" || volume < 0 || volume > 1) throw new Error("volume must be a number between 0 and 1");
      if (sound) await sound.update({volume}); else await playlist.update({volume});
      return {playlist: serializePlaylist(playlist)};
    },
    playSound: (d) => {
      if (!d.src) throw new Error("src is required");
      const volume = d.volume ?? 0.5, loop = d.loop ?? false;
      // Fire-and-forget, as the module does: local playback can hang in headless Chrome.
      foundry.audio.AudioHelper.play({src: d.src, volume, loop}, true);
      return {src: d.src, volume, loop};
    },
  };
  return true;
}
