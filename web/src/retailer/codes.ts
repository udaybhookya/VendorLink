import { c } from "../lib/i18n";

/** A machine code (Guardian reason, listing flag) in words for retail staff; the code itself
 *  stays available as a tooltip. Codes with a variable tail (ATTRIBUTE_MISSING_SIZE,
 *  MISSING_REGISTRATION_PIRT) use their prefix's sentence. */
export function explain(code: string): string {
  const exact = c(`codes.${code}`);
  if (!exact.startsWith("⟨")) return exact;
  for (const prefix of ["ATTRIBUTE_MISSING", "MISSING_REGISTRATION"]) {
    if (code.startsWith(`${prefix}_`)) {
      const x = code.slice(prefix.length + 1).toLowerCase().replace(/_/g, " ");
      return c(`codes.${prefix}`, { x: prefix === "MISSING_REGISTRATION" ? x.toUpperCase() : x });
    }
  }
  return code.replace(/_/g, " ").toLowerCase().replace(/^./, (s) => s.toUpperCase());
}
