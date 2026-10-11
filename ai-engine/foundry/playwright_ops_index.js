() => {
  // search and structure (module: utils/searchIndex.ts, utils/search.ts, routers/search.ts, routers/structure.ts).
  // No user is sent, so the module's permission filtering is a no-op here; the GM sees everything.
  const SEARCH_TAGLINES = {Actor: "Actors Directory", Item: "Items Directory", Scene: "Scenes Directory",
    JournalEntry: "Journal Directory", RollTable: "Roll Tables Directory", Cards: "Cards Directory",
    Macro: "Macros Directory", Playlist: "Playlists Directory"};
  const worldSearchEntries = () => {
    const sources = {Actor: game.actors, Item: game.items, Scene: game.scenes, JournalEntry: game.journal,
      RollTable: game.tables, Cards: game.cards, Macro: game.macros, Playlist: game.playlists};
    const out = [];
    for (const [documentType, collection] of Object.entries(sources)) {
      for (const e of collection?.contents ?? []) {
        if (!e.uuid || !e.name) continue;
        out.push({documentType, id: e.id, name: e.name, uuid: e.uuid, folder: e.folder?.id ?? null,
          pack: null, packageName: null, subType: e.type || "", icon: e.img || e.thumbnail || "",
          tagline: SEARCH_TAGLINES[documentType] ?? `${documentType}s Directory`, resultType: "WorldEntity"});
      }
    }
    return out;
  };
  let compendiumEntriesCache = null;  // the module also indexes compendiums once per session
  const compendiumSearchEntries = async () => {
    if (compendiumEntriesCache) return compendiumEntriesCache;
    compendiumEntriesCache = [];
    for (const pack of game.packs.contents) {
      try {
        const index = await pack.getIndex();
        for (const entry of index.contents) {
          compendiumEntriesCache.push({documentType: pack.documentName, id: entry._id,
            name: entry.name, uuid: entry.uuid ?? `${pack.collection}.${entry._id}`, folder: null,
            pack: pack.collection, packageName: pack.title, subType: entry.type || "", icon: entry.img || "",
            tagline: pack.title, resultType: "CompendiumEntity"});
        }
      } catch (err) {
        console.warn("[aigm] search: failed to index compendium", pack.collection, err);
      }
    }
    return compendiumEntriesCache;
  };
  const scoreMatch = (name, query) => {
    if (!name || !query) return null;
    const nameLower = name.toLowerCase(), queryLower = query.toLowerCase();
    if (nameLower === queryLower) return {score: 100, formattedMatch: `<strong>${name}</strong>`};
    if (nameLower.startsWith(queryLower)) {
      return {score: 80, formattedMatch: `<strong>${name.slice(0, query.length)}</strong>${name.slice(query.length)}`};
    }
    const idx = nameLower.indexOf(queryLower);
    if (idx !== -1) {
      return {score: 60, formattedMatch: `${name.slice(0, idx)}<strong>${name.slice(idx, idx + query.length)}</strong>${name.slice(idx + query.length)}`};
    }
    for (const word of name.split(/\s+/)) {
      if (word.toLowerCase().startsWith(queryLower)) return {score: 40, formattedMatch: name};
    }
    let qi = 0;
    for (let ni = 0; ni < nameLower.length && qi < queryLower.length; ni++) {
      if (nameLower[ni] === queryLower[qi]) qi++;
    }
    return qi === queryLower.length ? {score: 20, formattedMatch: name} : null;
  };
  const parseFilterString = (s) => {
    if (!s.includes(":")) return {documentType: s};
    const filters = {};
    for (const part of s.split(",")) {
      if (!part.includes(":")) continue;
      const [key, value] = part.split(":");
      if (key && value) filters[key.trim()] = value.trim();
    }
    return filters;
  };
  const matchesFilters = (entry, filters) => {
    for (const [key, value] of Object.entries(filters)) {
      if (!value) continue;
      if (key === "resultType") {
        if (!entry.resultType || entry.resultType.toLowerCase() !== value.toLowerCase()) return false;
      } else if (key === "package") {
        const pack = entry.pack;
        if (!pack) return false;
        if (pack.toLowerCase() !== value.toLowerCase() && `Compendium.${pack}`.toLowerCase() !== value.toLowerCase()) return false;
      } else if (key === "folder") {
        const folder = entry.folder;
        if (!folder) return false;
        if (!(value === folder || value === `Folder.${folder}` || `Folder.${value}` === folder)) return false;
      } else {
        const pv = entry[key];
        if (pv === undefined || (typeof pv === "string" && pv.toLowerCase() !== value.toLowerCase())) return false;
      }
    }
    return true;
  };
  const structureOf = async (d) => {
    const includeEntityData = d.includeEntityData ?? false;
    const path = d.path || null;
    const recursive = d.recursive ?? false;
    const recursiveDepth = d.recursiveDepth ?? 5;
    const rawTypes = d.types || d.type;
    const types = rawTypes
      ? (Array.isArray(rawTypes) ? rawTypes : String(rawTypes).split(",").map(t => t.trim()).filter(Boolean))
      : ["Scene", "Actor", "Item", "JournalEntry", "RollTable", "Cards", "Macro", "Playlist"];
    const typeCollections = {Scene: game.scenes, Actor: game.actors, Item: game.items, JournalEntry: game.journal,
      RollTable: game.tables, Cards: game.cards, Macro: game.macros, Playlist: game.playlists};
    const entityData = (e) => includeEntityData ? e.toObject(false)
      : {uuid: e.uuid, name: e.name, id: e.id, type: e.documentName};
    const filtered = (entities) => entities.filter(e => types.includes(e.documentName)).map(entityData);
    const buildFolders = (parent, depth = 0) => {
      if (!recursive || depth >= recursiveDepth) return {};
      const out = {};
      const children = (game.folders?.contents ?? []).filter(f => f.folder?.id === parent?.id && types.includes(f.type));
      for (const folder of children) {
        const key = folder.name || folder.id;
        out[key] = {id: folder.id, uuid: folder.uuid, type: folder.type, ...buildFolders(folder, depth + 1)};
        const ents = filtered(folder.contents);
        if (ents.length) out[key].entities = ents;
      }
      return out;
    };
    if (path?.startsWith("Compendium.")) {
      const pack = game.packs.get(path.replace("Compendium.", ""));
      if (!pack) throw new Error(`Compendium not found: ${path}`);
      const index = await pack.getIndex();
      const entities = index.contents.map(e => includeEntityData ? {...e}
        : {uuid: e.uuid || `${pack.collection}.${e._id}`, name: e.name, id: e._id, type: pack.documentName});
      return {compendium: {name: pack.title, type: pack.documentName, entities}};
    }
    if (path?.startsWith("Folder.")) {
      const match = path.match(/Folder\.([a-zA-Z0-9]+)/);
      if (!match) throw new Error(`Invalid folder path: ${path}`);
      const start = game.folders?.get(match[1]);
      if (!start) throw new Error(`Folder not found: ${path}`);
      if (!types.includes(start.type)) throw new Error(`Folder type ${start.type} not included in requested types`);
      const result = {folders: buildFolders(start)};
      const ents = filtered(start.contents);
      if (ents.length) result.entities = ents;
      return result;
    }
    const result = {};
    if (recursive) {
      result.folders = buildFolders(null);
    } else {
      result.folders = {};
      for (const type of types) {
        for (const folder of (game.folders?.contents ?? []).filter(f => f.type === type && !f.folder)) {
          const key = folder.name || folder.id;
          result.folders[key] = {id: folder.id, uuid: folder.uuid, type: folder.type};
          const ents = filtered(folder.contents);
          if (ents.length) result.folders[key].entities = ents;
        }
      }
    }
    const rootEntities = {};
    for (const type of types) {
      const collection = typeCollections[type];
      if (!collection) continue;
      const ents = collection.filter(e => !e.folder).map(entityData);
      if (ents.length) rootEntities[type.toLowerCase() + "s"] = ents;
    }
    if (Object.keys(rootEntities).length) result.entities = rootEntities;
    if (!path) {
      const packs = {};
      for (const pack of game.packs.contents) {
        if (!types.includes(pack.documentName)) continue;
        const key = pack.title || pack.collection;
        packs[key] = {id: pack.collection, name: pack.title, type: pack.documentName, uuid: `Compendium.${pack.collection}`};
        try {
          const index = await pack.getIndex();
          const ents = index.contents.map(e => includeEntityData ? {...e}
            : {uuid: e.uuid || `${pack.collection}.${e._id}`, name: e.name, id: e._id, type: e.type || pack.documentName});
          if (ents.length) packs[key].entities = ents;
        } catch (err) {
          console.warn("[aigm] structure: failed to load compendium", pack.collection, err);
        }
      }
      if (Object.keys(packs).length) result.compendiumPacks = packs;
    }
    return result;
  };
  Object.assign(window.__aigmOps, {
    search: async (d) => {
      if (d.ownedByUserId) throw new Error("ownedByUserId is not ported to the playwright backend yet");
      const limit = Math.min(parseInt(d.limit, 10) || 200, 500);
      const excludeCompendiums = d.excludeCompendiums === true || d.excludeCompendiums === "true";
      const minified = d.minified === true || d.minified === "true";
      const filters = d.filter ? (typeof d.filter === "string" ? parseFilterString(d.filter) : d.filter) : null;
      const entries = worldSearchEntries();
      if (!excludeCompendiums) entries.push(...(await compendiumSearchEntries()));
      const scored = [];
      for (const entry of entries) {
        if (filters && !matchesFilters(entry, filters)) continue;
        if (d.query) {
          const m = scoreMatch(entry.name, d.query);
          if (m) scored.push({entry, score: m.score, formattedMatch: m.formattedMatch});
        } else {
          scored.push({entry, score: 0, formattedMatch: entry.name});
        }
      }
      scored.sort((a, b) => b.score - a.score || a.entry.name.localeCompare(b.entry.name));
      const seen = new Set();
      const results = [];
      for (const {entry, formattedMatch} of scored.slice(0, limit)) {
        if (entry.uuid && seen.has(entry.uuid)) continue;
        if (entry.uuid) seen.add(entry.uuid);
        results.push(minified
          ? {uuid: entry.uuid, id: entry.id, name: entry.name, img: entry.icon, documentType: entry.documentType}
          : {documentType: entry.documentType, folder: entry.folder, id: entry.id, name: entry.name,
              package: entry.pack, packageName: entry.packageName, subType: entry.subType, uuid: entry.uuid,
              icon: entry.icon, journalLink: `@UUID[${entry.uuid}]{${entry.name}}`, tagline: entry.tagline,
              formattedMatch, resultType: entry.resultType});
      }
      return {query: d.query, filter: d.filter, results};
    },
    structure: (d) => structureOf(d),
  });
  return true;
}
