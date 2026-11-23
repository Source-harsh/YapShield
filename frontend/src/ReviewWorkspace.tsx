import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import {
  bulkEligible, exactOccurrences, reviewShortcut, sourceSegments, sourceSelection,
  type Candidate, type Decision, type Decisions, type Links, type LinkStatus,
} from "./review";

const linkLabels = { confirm: "Confirm link", reject: "Reject link", detach: "Detach mention" } as const;
const linkChoices = ["confirm", "reject", "detach"] as const;
const decisionsToShow = ["redact", "keep"] as const;

function decisionLabel(decision: Decision | undefined) {
  if (decision === "redact") return "Approved for redaction";
  if (decision === "keep") return "Kept";
  return "Detected";
}

function linkLabel(candidate: Candidate, links: Links) {
  switch (links[candidate.id]) {
    case "confirm": return "Linked identity";
    case "reject": return "Link rejected";
    case "detach": return "Detached from identity";
  }
  if (candidate.proposal) return "Unresolved identity proposal";
  if (candidate.identity?.uncertainty === "uncertain") return "Uncertain identity · Separate mention";
  if (candidate.identity?.alternates.some((link) => links[link.mention_id] === "confirm")) return "Linked identity";
}

type Props = {
  text: string;
  candidates: Candidate[];
  busy: boolean;
  decisions: Decisions;
  links: Links;
  onDecide: (ids: string[], decision: Decision, pendingOnly?: boolean) => void;
  onLink: (id: string, status: LinkStatus) => void;
  sourceRef: RefObject<HTMLPreElement | null>;
  children: ReactNode;
};

export function ReviewWorkspace({ text, candidates, busy, decisions, links, onDecide, onLink, sourceRef, children }: Props) {
  const byId = useMemo(() => new Map(candidates.map((candidate) => [candidate.id, candidate])), [candidates]);
  const codePoints = useMemo(() => Array.from(text), [text]);
  const segments = useMemo(() => sourceSegments(codePoints, candidates), [codePoints, candidates]);
  const [selectedId, setSelectedId] = useState(candidates[0]?.id ?? "");
  const [help, setHelp] = useState(false);
  const [shortcutsEnabled, setShortcutsEnabled] = useState(true);
  const candidateList = useRef<HTMLOListElement>(null);
  const selected = byId.get(selectedId);
  const selectedIndex = candidates.findIndex(({ id }) => id === selectedId);
  const proposed = candidates.filter((candidate) => candidate.proposal && !links[candidate.id]).length;
  const remaining = candidates.filter(({ id }) => !decisions[id]).length;

  function select(id: string, from: "source" | "review" | "navigation") {
    setSelectedId(id);
    const item = document.getElementById(`review-${id}`);
    const first = segments.find(({ ids }) => ids.includes(id));
    const source = first && document.getElementById(`source-${first.start}`);
    if (from === "source") {
      item?.scrollIntoView({ block: "nearest" });
      item?.focus({ preventScroll: true });
    } else {
      source?.scrollIntoView({ block: "nearest" });
      if (item && candidateList.current) {
        const bounds = candidateList.current.getBoundingClientRect();
        const row = item.getBoundingClientRect();
        if (row.top < bounds.top) candidateList.current.scrollTop += row.top - bounds.top;
        if (row.bottom > bounds.bottom) candidateList.current.scrollTop += row.bottom - bounds.bottom;
      }
      if (from === "navigation" && candidateList.current?.contains(document.activeElement)) {
        item?.focus({ preventScroll: true });
      }
    }
  }

  function navigate(step: number) {
    const next = candidates[selectedIndex + step];
    if (next) select(next.id, "navigation");
  }

  useEffect(() => {
    if (busy || !shortcutsEnabled) return;
    function onKeyDown(event: KeyboardEvent) {
      const key = reviewShortcut(event);
      if (!key) return;
      event.preventDefault();
      if (key === "?") setHelp((open) => !open);
      if (key === "j") navigate(1);
      if (key === "k") navigate(-1);
      if (selected && (key === "r" || key === "s")) onDecide([selected.id], key === "r" ? "redact" : "keep");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  function listOccurrences(occurrences: Candidate[]) {
    return <ul className="occurrence-list">{occurrences.map((mention) => (
      <li key={mention.id}>
        <button type="button" onClick={() => select(mention.id, "review")}>
          {mention.text} · {mention.kind} · Line {mention.line}
        </button>
        <span className="muted">{decisionLabel(decisions[mention.id])}{linkLabel(mention, links) && ` · ${linkLabel(mention, links)}`}</span>
      </li>
    ))}</ul>;
  }

  function bulkControls(occurrences: Candidate[]) {
    const pending = occurrences.filter((mention) => bulkEligible(mention, links) && !decisions[mention.id]);
    return <div className="controls">
      {decisionsToShow.map((choice) => <button type="button" key={choice} disabled={busy || pending.length === 0}
        onClick={() => onDecide(pending.map(({ id }) => id), choice, true)}>
        {choice === "redact" ? "Approve" : "Keep"} {pending.length} pending {pending.length === 1 ? "mention" : "mentions"}
      </button>)}
    </div>;
  }

  const exact = selected ? exactOccurrences(selected, candidates) : [];
  const confirmed = selected?.identity?.mention_ids.flatMap((id) => {
    const mention = byId.get(id);
    return mention && (!mention.proposal || links[id] === "confirm") ? [mention] : [];
  }) ?? [];

  return <div className="workspace">
    <article className="panel">
      <h2>Extracted source</h2>
      <pre ref={sourceRef} tabIndex={0} aria-label="Extracted source with detected mentions">{segments.map(({ start, text, ids }) => {
        if (!ids.length) return <span key={start}>{text}</span>;
        const active = ids.includes(selectedId);
        const mention = byId.get(active ? selectedId : ids[0])!;
        const choose = () => { if (!busy) select(sourceSelection(ids, selectedId), "source"); };
        const description = ids.length > 1 ? ` · ${ids.length} overlapping mentions, activate to cycle` : "";
        return <span key={start} id={`source-${start}`} className="source-mention" data-kind={mention.kind}
          data-decision={decisions[mention.id]} data-overlap={ids.length > 1} role="button" tabIndex={busy ? -1 : 0}
          aria-pressed={active} aria-disabled={busy} aria-label={`${mention.kind} · ${mention.text} · Line ${mention.line}${description}`}
          onClick={() => { if (window.getSelection()?.isCollapsed !== false) choose(); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); }
          }}>{text}</span>;
      })}</pre>
    </article>
    <article className="panel">
      <div className="heading"><h2>PII mentions</h2><span className="muted">{remaining} pending · {proposed} unresolved links</span></div>
      <div className="controls mention-navigation" aria-label="Mention navigation" role="group">
        <button type="button" disabled={busy || selectedIndex <= 0} onClick={() => navigate(-1)} aria-keyshortcuts={shortcutsEnabled ? "K" : undefined}>Previous candidate</button>
        <button type="button" disabled={busy || selectedIndex >= candidates.length - 1} onClick={() => navigate(1)} aria-keyshortcuts={shortcutsEnabled ? "J" : undefined}>Next candidate</button>
        <button type="button" aria-expanded={help} aria-controls="review-shortcut-help" onClick={() => setHelp(!help)} aria-keyshortcuts={shortcutsEnabled ? "?" : undefined}>Keyboard shortcuts</button>
      </div>
      <div id="review-shortcut-help" hidden={!help} className="shortcut-help">
        <p><kbd>J</kbd> Next · <kbd>K</kbd> Previous · <kbd>R</kbd> Approve · <kbd>S</kbd> Keep · <kbd>?</kbd> Toggle help</p>
        <label><input type="checkbox" checked={shortcutsEnabled} onChange={(event) => setShortcutsEnabled(event.target.checked)} /> Enable letter shortcuts</label>
      </div>
      {!candidates.length && <p className="muted">No candidates found. Inspect the source for missed information.</p>}
      <ol ref={candidateList} className="candidate-list mention-list" aria-label="Detected mentions">
        {candidates.map((mention) => <li key={mention.id}>
          <button id={`review-${mention.id}`} type="button" aria-current={selectedId === mention.id || undefined}
            disabled={busy} onClick={() => select(mention.id, "review")}>
            <span>{mention.text}</span>
            <span className="muted">{mention.kind} · Line {mention.line} · {decisionLabel(decisions[mention.id])}</span>
            {linkLabel(mention, links) && <span className="muted">{linkLabel(mention, links)}</span>}
          </button>
        </li>)}
      </ol>
      {selected && <section className="mention-detail" aria-label="Selected mention">
        <h3>{selected.kind} · Line {selected.line}</h3>
        <p className="muted" role="status">{selectedIndex + 1} of {candidates.length} · {decisionLabel(decisions[selectedId])}{linkLabel(selected, links) && ` · ${linkLabel(selected, links)}`}</p>
        <div className="identity-context">
          {codePoints.slice(Math.max(0, selected.start - 40), selected.start).join("")}
          <mark>{selected.text}</mark>
          {codePoints.slice(selected.end, selected.end + 40).join("")}
        </div>
        <p className="muted">{selected.evidence.map(({ source, rule }) => `${source} (${rule})`).join(" · ") || selected.source}</p>
        <div className="controls" role="group" aria-label="Selected mention decision">
          {decisionsToShow.map((choice) => <button type="button" key={choice} disabled={busy} aria-pressed={decisions[selectedId] === choice}
            aria-keyshortcuts={shortcutsEnabled ? choice === "redact" ? "R" : "S" : undefined} onClick={() => onDecide([selectedId], choice)}>
            {choice === "redact" ? "Approve selected mention" : "Keep selected mention"}
          </button>)}
        </div>
        {selected.proposal && <div className="mention-proposal">
          <p className="muted">{selected.proposal.evidence}.</p>
          <div className="controls" role="group" aria-label="Identity link decision">
            {linkChoices.map((choice) => <button type="button" key={choice} disabled={busy} aria-pressed={links[selectedId] === choice}
              onClick={() => onLink(selectedId, choice)}>{linkLabels[choice]}</button>)}
          </div>
        </div>}
        {selected.identity?.uncertainty === "uncertain" && <div className="mention-proposal">
          <p className="muted">{selected.identity.evidence}.</p>
          {listOccurrences(selected.identity.possible_primary_ids.flatMap((id) => {
            const mention = byId.get(id);
            return mention ? [mention] : [];
          }))}
          {selected.identity.possible_primary_count > selected.identity.possible_primary_ids.length &&
            <p className="muted">{selected.identity.possible_primary_count - selected.identity.possible_primary_ids.length} more possible matches</p>}
        </div>}
        {exact.length > 1 && <details className="mention-proposal">
          <summary>Review {exact.length} exact occurrences</summary>
          {listOccurrences(exact)}
          <p className="muted">Bulk choices apply to pending exact matches with a confirmed link or no identity ambiguity.</p>
          {bulkControls(exact)}
        </details>}
        {confirmed.length > 1 && (!selected.proposal || links[selected.id] === "confirm") && <details className="mention-proposal">
          <summary>Review {confirmed.length} confirmed identity mentions</summary>
          {listOccurrences(confirmed)}
          {bulkControls(confirmed)}
        </details>}
      </section>}
      {children}
    </article>
  </div>;
}
