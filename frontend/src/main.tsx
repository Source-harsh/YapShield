import { motion, useReducedMotion } from "framer-motion";
import { useMemo, useRef, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Decision = "redact" | "keep";
type Mention = {
  id: string;
  text: string;
  kind: string;
  start: number;
  end: number;
  source: string;
  line: number;
  evidence: { source: string; rule: string }[];
};
type LinkStatus = "confirm" | "reject" | "detach";
type LinkDecision = { status: LinkStatus; decision?: Decision };
type IdentityLink = {
  mention_id: string;
  relationship: string;
  evidence: string;
  uncertainty: "proposed";
  status: "pending";
};
type Identity = {
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
type Review = { token: string; text: string; groups: Group[] };
type Release = { text: string; summary: string; pdf: string | null; pdf_error: string | null };

const linkLabels: Record<LinkStatus, string> = { confirm: "Confirm link", reject: "Reject link", detach: "Detach mention" };
const linkStatuses: LinkStatus[] = ["confirm", "reject", "detach"];

function linkResolved(link: LinkDecision | undefined) {
  return !!link && (link.status === "confirm" || !!link.decision);
}

async function request<T>(path: string, options: RequestInit): Promise<T> {
  const response = await fetch(path, { ...options, cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request failed.");
  return data as T;
}

function download(payload: Blob, filename: string) {
  const url = URL.createObjectURL(payload);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function DocumentSculpture() {
  return (
    <div className="sculpture" aria-hidden="true">
      <div className="sculpture-frame">
        <div className="document-stack">
          <div className="sheet sheet-back" />
          <div className="sheet sheet-middle" />
          <div className="sheet sheet-face">
            <i className="sheet-title" />
            <i className="sheet-rule" />
            <i className="sheet-redaction" />
            <i className="sheet-rule" />
            <i className="sheet-rule sheet-rule-short" />
            <i className="sheet-redaction sheet-redaction-short" />
            <i className="sheet-rule sheet-rule-short" />
          </div>
        </div>
      </div>
    </div>
  );
}

function App() {
  const [review, setReview] = useState<Review>();
  const [release, setRelease] = useState<Release>();
  const [decisions, setDecisions] = useState<Record<number, Decision>>({});
  const [links, setLinks] = useState<Record<string, LinkDecision>>({});
  const [manual, setManual] = useState<string[]>([]);
  const [literal, setLiteral] = useState("");
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const original = useRef<HTMLPreElement>(null);
  const codePoints = useMemo(() => Array.from(review?.text ?? ""), [review?.text]);
  const mentions = useMemo(() => new Map(review?.groups.flatMap((group) =>
    group.mentions.map((mention) => [mention.id, mention] as const))), [review]);
  const reduceMotion = useReducedMotion();
  const remaining = (review?.groups.length ?? 0) - Object.keys(decisions).length;
  const active = release ? "Export" : review ? "Review" : "Import";

  async function upload(file: File) {
    setError("");
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension !== "txt" && extension !== "pdf") {
      setError("Choose a TXT or text-based PDF file.");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("Choose a file no larger than 8 MB.");
      return;
    }
    setBusy(true);
    try {
      const result = await request<Review>("/api/review", {
        method: "POST", body: file,
        headers: { "Content-Type": extension === "pdf" ? "application/pdf" : "text/plain" },
      });
      setFileName(file.name);
      setReview(result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError("");
    try {
      if (review) await request<void>("/api/review", {
        method: "DELETE", headers: { "X-Review-Token": review.token },
      });
      setReview(undefined);
      setRelease(undefined);
      setFileName("");
      setDecisions({});
      setLinks({});
      setManual([]);
      setLiteral("");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<Release>("/api/release", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Review-Token": review.token },
        body: JSON.stringify({ decisions, manual, links }),
      });
      setRelease(result);
      setReview(undefined);
      setFileName("");
      setDecisions({});
      setLinks({});
      setManual([]);
      setLiteral("");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function addManual(event: FormEvent) {
    event.preventDefault();
    if (!literal.trim() || manual.includes(literal)) return;
    setManual([...manual, literal]);
    setLiteral("");
  }

  function setLink(groupId: number, mentionId: string, link: LinkDecision) {
    setLinks((current) => ({ ...current, [mentionId]: link }));
    setDecisions((current) => {
      const next = { ...current };
      delete next[groupId];
      return next;
    });
  }

  return (
    <div className="shell">
      <header className="bar">
        <div className="brand"><span className="mark" aria-hidden="true">P</span> PrivyDoc</div>
        <span className="local"><i /> Local workspace</span>
      </header>
      <main className={active === "Import" ? "import-main" : undefined}>
        <nav aria-label="Workflow" className="flow">
          {["Import", "Review", "Export"].map((step) =>
            <span key={step} aria-current={step === active ? "step" : undefined}>{step}</span>
          )}
        </nav>
        {error && <p className="error" role="alert">{error}</p>}
        <motion.div key={active} initial={reduceMotion ? false : { opacity: 0, y: 7 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}>
          {!review && !release && (
            <section className="import">
              <div className="import-copy">
                <h1>Prepare a document <em>for release.</em></h1>
                <p className="lead">Review personal information, apply redactions, and export a sanitized document with a local summary.</p>
                <div className="import-action">
                  <label className="upload">
                    <span className="upload-symbol" aria-hidden="true">+</span>
                    <strong>{busy ? "Reading document…" : "Choose a document"}</strong>
                    <input type="file" accept=".txt,.pdf" disabled={busy} aria-describedby="file-limits"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) void upload(file);
                      }} />
                  </label>
                  <p id="file-limits">UTF-8 TXT or text-based PDF · Up to 8 MB</p>
                </div>
              </div>
              <DocumentSculpture />
            </section>
          )}
          {review && (
            <section>
              <div className="heading"><div><h1>Review candidates</h1><p className="filename">{fileName}</p></div>
                <button className="secondary" disabled={busy} onClick={() => void reset()}>Close review</button>
              </div>
              <div className="workspace">
                <article className="panel">
                  <h2>Extracted source</h2>
                  <pre ref={original} tabIndex={0}>{review.text}</pre>
                </article>
                <article className="panel">
                  <div className="heading"><h2>Candidate groups</h2><span className="muted">{remaining} pending</span></div>
                  <div className="candidate-list">
                    {review.groups.length === 0 && <p className="muted">No candidates found. Inspect the source for missed information.</p>}
                    {review.groups.map((group) => {
                      const identity = group.identity;
                      const ready = identity?.alternates.every((link) => linkResolved(links[link.mention_id])) ?? true;
                      const confirmed = identity?.alternates.filter((link) => links[link.mention_id]?.status === "confirm").length ?? 0;
                      return <fieldset className="candidate" key={group.id} disabled={busy}>
                        <legend>{group.kind} · {group.mentions.length} {group.mentions.length === 1 ? "mention" : "mentions"}</legend>
                        {identity?.uncertainty === "uncertain" && <p className="muted">Separate mention. {identity.evidence}.</p>}
                        <details open={identity?.alternates.length ? true : undefined}>
                          <summary>{group.mentions[0].text} · Preview occurrences</summary>
                          {identity?.uncertainty === "uncertain" && <ul>
                            {identity.possible_primary_ids.map((id) => {
                              const other = mentions.get(id);
                              return other && <li key={id}>Possible match {other.text} · Line {other.line}</li>;
                            })}
                            {identity.possible_primary_count > identity.possible_primary_ids.length &&
                              <li>{identity.possible_primary_count - identity.possible_primary_ids.length} more possible matches</li>}
                          </ul>}
                          <ul>{group.mentions.map((mention) => {
                            const proposal = identity?.alternates.find((link) => link.mention_id === mention.id);
                            const choice = links[mention.id];
                            return <li key={mention.id} className="identity-mention">
                              {mention.text} <span className="muted">· Line {mention.line} · {mention.evidence.map(({ source, rule }) => `${source} (${rule})`).join(" · ") || mention.source}</span>
                              {identity && <div className="identity-context">
                                {codePoints.slice(Math.max(0, mention.start - 35), mention.start).join("")}
                                <mark>{mention.text}</mark>
                                {codePoints.slice(mention.end, mention.end + 35).join("")}
                              </div>}
                              {proposal && <>
                                <p className="muted">{proposal.evidence}.</p>
                                <div className="controls" aria-label={`Link decision for ${mention.text} on line ${mention.line}`} role="group">
                                  {linkStatuses.map((status) => <button key={status} aria-pressed={choice?.status === status}
                                    onClick={() => setLink(group.id, mention.id, { status })}>{linkLabels[status]}</button>)}
                                </div>
                                {choice && choice.status !== "confirm" && <div className="controls" aria-label="Separate mention decision" role="group">
                                  {(["redact", "keep"] as const).map((decision) => <button key={decision} aria-pressed={choice.decision === decision}
                                    onClick={() => setLink(group.id, mention.id, { ...choice, decision })}>
                                    {decision === "redact" ? "Redact mention" : "Keep mention"}
                                  </button>)}
                                </div>}
                              </>}
                            </li>;
                          })}</ul>
                        </details>
                        <div className="controls">
                          {(["redact", "keep"] as const).map((choice) => (
                            <button key={choice} disabled={!ready} aria-pressed={decisions[group.id] === choice} onClick={() =>
                              setDecisions({ ...decisions, [group.id]: choice })}>
                              {choice === "redact" ? "Redact" : "Keep"} {identity ? confirmed ? "confirmed group" : "mention" : "group"}
                            </button>
                          ))}
                        </div>
                      </fieldset>;
                    })}
                  </div>
                  <form className="manual" onSubmit={addManual}>
                    <label htmlFor="literal">Manual redactions</label>
                    <p className="muted">Add exact text to remove all case-insensitive matches.</p>
                    <div className="controls">
                      <textarea id="literal" rows={1} value={literal} onChange={(event) => setLiteral(event.target.value)}
                        minLength={2} maxLength={500} disabled={busy || manual.length >= 50} />
                      <button disabled={busy || literal.trim().length < 2 || manual.length >= 50}>Add</button>
                      <button type="button" disabled={busy || manual.length >= 50} onClick={() => {
                        const selection = window.getSelection();
                        if (selection?.anchorNode && selection.focusNode && original.current?.contains(selection.anchorNode)
                          && original.current.contains(selection.focusNode)) {
                          setLiteral(selection.toString().slice(0, 500));
                        }
                      }}>Use selection</button>
                    </div>
                    {manual.map((item, index) => <div className="manual-item" key={item}><span>{item}</span>
                      <button type="button" disabled={busy} onClick={() => setManual(manual.filter((_, i) => i !== index))}>Remove</button>
                    </div>)}
                  </form>
                </article>
              </div>
              <div className="finish">
                <p className="muted">Review the full source before release. Candidate detection can miss sensitive information.</p>
                <button className="primary" disabled={busy || remaining > 0 || literal.trim().length > 0}
                  onClick={() => void finalize()}>{busy ? "Working…" : "Apply redactions"}</button>
              </div>
            </section>
          )}
          {release && (
            <section>
              <div className="heading"><div><h1>Release files</h1><p>Checked for approved values in the generated outputs.</p></div>
                <button className="secondary" onClick={() => void reset()}>New document</button>
              </div>
              <div className="workspace">
                <article className="panel"><h2>Sanitized document</h2><pre tabIndex={0}>{release.text}</pre>
                  <div className="controls exports">
                    <button className="primary" onClick={() => download(new Blob([release.text], { type: "text/plain;charset=utf-8" }), "sanitized.txt")}>Download TXT</button>
                    {release.pdf && <button onClick={() => download(new Blob([
                      Uint8Array.from(atob(release.pdf!), (character) => character.charCodeAt(0)),
                    ], { type: "application/pdf" }), "sanitized.pdf")}>Download PDF</button>}
                  </div>
                  {release.pdf_error && <p className="muted">{release.pdf_error}</p>}
                </article>
                <article className="panel"><h2>Extractive summary</h2>
                  {release.summary ? <><pre tabIndex={0}>{release.summary}</pre><div className="exports">
                    <button onClick={() => download(new Blob([release.summary], { type: "text/plain;charset=utf-8" }), "summary.txt")}>Download summary</button>
                  </div></> : <p className="muted">No summary is available for this release.</p>}
                </article>
              </div>
            </section>
          )}
        </motion.div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
