import assert from "node:assert/strict";
import test from "node:test";
import { applyPending, bulkEligible, exactOccurrences, sourceSegments, sourceSelection } from "../src/review.ts";

test("Unicode code points, overlap and exclusive adjacent ends preserve all source text", () => {
  const text = "🙂Alpha\nBé";
  const segments = sourceSegments(Array.from(text), [
    { id: "wide", start: 1, end: 6 },
    { id: "overlap", start: 4, end: 8 },
    { id: "adjacent", start: 8, end: 9 },
  ]);
  assert.deepEqual(segments, [
    { start: 0, text: "🙂", ids: [] },
    { start: 1, text: "Alp", ids: ["wide"] },
    { start: 4, text: "ha", ids: ["wide", "overlap"] },
    { start: 6, text: "\nB", ids: ["overlap"] },
    { start: 8, text: "é", ids: ["adjacent"] },
  ]);
  assert.equal(segments.map(({ text }) => text).join(""), text);
  assert.equal(sourceSelection(segments[2].ids, "wide"), "overlap");
  assert.equal(sourceSelection(segments[2].ids, "overlap"), "wide");
});

test("bulk decisions require exact values, eligible links and a pending decision", () => {
  const selected = { id: "first", text: "Jane" };
  const kept = { id: "kept", text: "Jane" };
  const proposal = { id: "proposal", text: "Jane", proposal: {} };
  const ambiguous = { id: "ambiguous", text: "Jane", identity: { uncertainty: "uncertain" } };
  const candidates = [selected, kept, proposal, ambiguous, { id: "case", text: "JANE" }, { id: "similar", text: "Janet" }];
  const exact = exactOccurrences(selected, candidates);
  assert.deepEqual(exact, [selected, kept, proposal, ambiguous]);
  const pendingIds = exact.filter((item) => bulkEligible(item, {})).map(({ id }) => id);
  assert.deepEqual(applyPending({ kept: "keep" }, pendingIds, "redact"), { kept: "keep", first: "redact" });
  assert.equal(bulkEligible(proposal, { proposal: "confirm" }), true);
  assert.equal(bulkEligible(proposal, { proposal: "detach" }), false);
  assert.equal(bulkEligible(ambiguous, {}), false);
  assert.deepEqual(applyPending({ first: "redact" }, ["first", "kept"], "keep"), { first: "redact", kept: "keep" });
});
