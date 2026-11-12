import { describe, expect, it } from "vitest";
// Generated from backend/app/domain/policy/rules.py by app.scripts.gen_docs (CI checks it is current).
import policyReference from "../../../docs/generated/policy_reference.md?raw";
import { en } from "./locales/en";
import { ja } from "./locales/ja";

type Tree = { [key: string]: string | Tree };

function leaves(tree: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out.set(path, value);
    else for (const [k, v] of leaves(value, path)) out.set(k, v);
  }
  return out;
}

const placeholders = (text: string) => [...text.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();

describe("translations", () => {
  const enLeaves = leaves(en as unknown as Tree);
  const jaLeaves = leaves(ja as unknown as Tree);

  it("Japanese has exactly the English keys", () => {
    expect([...jaLeaves.keys()].sort()).toEqual([...enLeaves.keys()].sort());
  });

  it("every Japanese string keeps the English placeholders", () => {
    const mismatched = [...enLeaves].filter(([key, text]) => placeholders(text).join() !== placeholders(jaLeaves.get(key) ?? "").join());
    expect(mismatched.map(([key]) => key)).toEqual([]);
  });

  it("no string is empty or left untranslated by mistake", () => {
    const empty = [...jaLeaves].filter(([, text]) => !text.trim()).map(([key]) => key);
    expect(empty).toEqual([]);
    // Japanese strings should contain Japanese script, apart from codes, formats and proper nouns.
    const allowedLatin = new Set(["requestDetail.policyEngine", "common.range", "p2p.quotes.scoreOf", "knowledge.sourcePlaceholder", "knowledge.placeholder", "notifications.channels.slack", "notifications.channels.teams"]);
    const latinOnly = [...jaLeaves]
      .filter(([key, text]) => !/[぀-ヿ㐀-鿿]/.test(text) && !allowedLatin.has(key))
      .filter(([, text]) => /[A-Za-z]{4,}/.test(text.replace(/\{\{\w+\}\}/g, "")));
    expect(latinOnly.map(([key]) => key)).toEqual([]);
  });

  it("has wording for exactly the backend's policy rules", () => {
    const backendIds = [...policyReference.matchAll(/^\| `([A-Z]+-\d{3})` \|/gm)].map((m) => m[1]).sort();
    expect(backendIds.length).toBeGreaterThan(0);
    expect(Object.keys(en.labels.rules).sort()).toEqual(backendIds);
    expect(Object.keys(ja.labels.rules).sort()).toEqual(backendIds);
  });
});
