// Generate TypeScript types from the JSON Schemas exported by scripts/export_schemas.py.
import { compile } from "json-schema-to-typescript";
import { readdir, readFile, writeFile } from "node:fs/promises";

const dir = new URL("../src/schemas/", import.meta.url);
const files = (await readdir(dir)).filter((f) => f.endsWith(".json")).sort();
const seen = new Set();

// Only models (root and $defs) get names; property titles would create colliding aliases.
function stripPropertyTitles(node, keep = false) {
  if (Array.isArray(node)) return node.forEach((n) => stripPropertyTitles(n));
  if (!node || typeof node !== "object") return;
  if (!keep) delete node.title;
  for (const [key, value] of Object.entries(node)) {
    if (key === "$defs") {
      for (const def of Object.values(value)) stripPropertyTitles(def, true);
    } else if (key === "properties") {
      // keys here are field names (a field may be called "title"): strip inside each, not here
      for (const prop of Object.values(value)) stripPropertyTitles(prop);
    } else if (key !== "title") {
      stripPropertyTitles(value);
    }
  }
}
let out = "/* Generated from vl_core.schemas by `npm run gen:types`. Do not edit. */\n\n";
for (const file of files) {
  const schema = JSON.parse(await readFile(new URL(file, dir), "utf8"));
  const name = schema.title ?? file.replace(".json", "");
  stripPropertyTitles(schema, true);
  const ts = await compile(schema, name, {
    bannerComment: "",
    additionalProperties: false,
    declareExternallyReferenced: true,
  });
  // Shared $defs are emitted with every schema; keep the first declaration of each.
  for (const block of ts.split(/\n(?=export )/)) {
    const name = block.match(/^export (?:interface|type) (\w+)/)?.[1];
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out += block.trim() + "\n\n";
  }
}
await writeFile(new URL("../src/types/generated.ts", import.meta.url), out);
console.log(`generated ${seen.size} types`);
