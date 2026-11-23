export type Decision = "redact" | "keep";
export type Mention = {
  id: string;
  text: string;
  kind: string;
  start: number;
  end: number;
  source: string;
  line: number;
  evidence: { source: string; rule: string }[];
};
export type LinkStatus = "confirm" | "reject" | "detach";
export type IdentityLink = {
  mention_id: string;
  relationship: string;
  evidence: string;
  uncertainty: "proposed";
  status: "pending";
};
export type Identity = {
  id: string;
  primary_mention_id: string;
  mention_ids: string[];
  alternates: IdentityLink[];
  relationship: string;
  possible_primary_ids: string[];
  possible_primary_count: number;
  evidence: string;
  uncertainty: "unlinked" | "proposed" | "uncertain";
  status: "pending" | "separate";
};
type Group = { id: number; kind: string; mentions: Mention[]; identity?: Identity | null };
export type Review = { token: string; text: string; groups: Group[] };
export type Decisions = Record<string, Decision | undefined>;
export type Links = Record<string, LinkStatus | undefined>;
export type Candidate = Mention & { identity?: Identity | null; proposal?: IdentityLink };

export function candidatesFor(review: Review): Candidate[] {
  return review.groups.flatMap(({ identity, mentions }) => {
    const proposals = new Map(identity?.alternates.map((link) => [link.mention_id, link]));
    return mentions.map((mention) => ({ ...mention, identity, proposal: proposals.get(mention.id) }));
  }).sort((a, b) => a.start - b.start || a.end - b.end || a.id.localeCompare(b.id));
}

type Boundary = { enter: string[]; leave: string[] };
type Span = Pick<Mention, "id" | "start" | "end">;

export function sourceSegments(codePoints: string[], spans: Span[]) {
  const boundaries = new Map<number, Boundary>([[0, { enter: [], leave: [] }], [codePoints.length, { enter: [], leave: [] }]]);
  function boundary(offset: number) {
    const existing = boundaries.get(offset);
    if (existing) return existing;
    const next: Boundary = { enter: [], leave: [] };
    boundaries.set(offset, next);
    return next;
  }
  for (const { id, start, end } of spans) {
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > codePoints.length || start >= end) continue;
    boundary(start).enter.push(id);
    boundary(end).leave.push(id);
  }
  const active = new Set<string>();
  const segments: { start: number; text: string; ids: string[] }[] = [];
  let previous = 0;
  for (const [offset, { enter, leave }] of [...boundaries].sort(([a], [b]) => a - b)) {
    if (previous < offset) segments.push({ start: previous, text: codePoints.slice(previous, offset).join(""), ids: [...active] });
    leave.forEach((id) => active.delete(id));
    enter.forEach((id) => active.add(id));
    previous = offset;
  }
  return segments;
}

export function sourceSelection(ids: string[], selected: string) {
  return ids[(ids.indexOf(selected) + 1) % ids.length];
}

export function exactOccurrences(selected: Candidate, candidates: Candidate[]) {
  return candidates.filter((candidate) => candidate.text === selected.text);
}

export function bulkEligible(candidate: Candidate, links: Links) {
  return candidate.identity?.uncertainty !== "uncertain" && (!candidate.proposal || links[candidate.id] === "confirm");
}

export function applyPending(decisions: Decisions, ids: string[], decision: Decision): Decisions {
  const next = { ...decisions };
  for (const id of ids) {
    if (!next[id]) next[id] = decision;
  }
  return next;
}

export function isEditable(target: EventTarget | null) {
  return target instanceof HTMLElement && (target.isContentEditable || !!target.closest("input, textarea, select, [role='textbox']"));
}

export function reviewShortcut(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || isEditable(event.target)) return;
  const key = event.key.toLowerCase();
  if (key === "j" || key === "k" || key === "r" || key === "s" || key === "?") return key;
}
