import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { ReviewWorkspace } from "./ReviewWorkspace";
import { applyPending, candidatesFor, type Decision, type Decisions, type Links, type LinkStatus, type Review } from "./review";
import "./style.css";

type Release = { text: string; txt: string; summary: string; pdf: string | null; pdf_error: string | null };
type Preview = { text: string; receipt: string };

function artifactBytes(encoded: string) {
  return Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
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
  const [decisions, setDecisions] = useState<Decisions>({});
  const [links, setLinks] = useState<Links>({});
  const [preview, setPreview] = useState<Preview>();
  const [manual, setManual] = useState<string[]>([]);
  const [literal, setLiteral] = useState("");
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const original = useRef<HTMLPreElement>(null);
  const previewPanel = useRef<HTMLElement>(null);
  const candidates = useMemo(() => review ? candidatesFor(review) : [], [review]);
  const reduceMotion = useReducedMotion();
  const ready = candidates.every(({ id, proposal }) => decisions[id] && (!proposal || links[id])) && !literal.trim();
  const active = release ? "Export" : review ? "Review" : "Import";

  useEffect(() => {
    if (preview !== undefined) previewPanel.current?.focus();
  }, [preview]);

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
      setPreview(undefined);
      setManual([]);
      setLiteral("");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function redactionRequest(review: Review, receipt?: string): RequestInit {
    return {
      method: "POST", headers: {
        "Content-Type": "application/json", "X-Review-Token": review.token,
        ...(receipt ? { "X-Preview-Receipt": receipt } : {}),
      },
      body: JSON.stringify({ mention_decisions: decisions, manual, links: Object.fromEntries(
        Object.entries(links).map(([id, status]) => [id, { status }]),
      ) }),
    };
  }

  async function previewRedactions() {
    if (!review || !ready) return;
    setBusy(true);
    setError("");
    setPreview(undefined);
    try {
      setPreview(await request<Preview>("/api/preview", redactionRequest(review)));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    if (!review || !ready || preview === undefined) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<Release>("/api/release", redactionRequest(review, preview.receipt));
      setRelease(result);
      setReview(undefined);
      setFileName("");
      setDecisions({});
      setLinks({});
      setPreview(undefined);
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
    setPreview(undefined);
  }

  function decide(ids: string[], decision: Decision, pendingOnly = false) {
    setDecisions((current) => pendingOnly ? applyPending(current, ids, decision) : { ...current, [ids[0]]: decision });
    setPreview(undefined);
  }

  function setLink(id: string, status: LinkStatus) {
    setLinks((current) => ({ ...current, [id]: status }));
    setPreview(undefined);
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
              <ReviewWorkspace text={review.text} candidates={candidates} busy={busy} decisions={decisions} links={links}
                onDecide={decide} onLink={setLink} sourceRef={original}>
                  <form className="manual" onSubmit={addManual}>
                    <label htmlFor="literal">Manual redactions</label>
                    <p className="muted">Add exact text to remove all case-insensitive matches.</p>
                    <div className="controls">
                      <textarea id="literal" rows={1} value={literal} onChange={(event) => { setLiteral(event.target.value); setPreview(undefined); }}
                        minLength={2} maxLength={500} disabled={busy || manual.length >= 50} />
                      <button disabled={busy || literal.trim().length < 2 || manual.length >= 50}>Add</button>
                      <button type="button" disabled={busy || manual.length >= 50} onClick={() => {
                        const selection = window.getSelection();
                        if (selection?.anchorNode && selection.focusNode && original.current?.contains(selection.anchorNode)
                          && original.current.contains(selection.focusNode)) {
                          setLiteral(selection.toString().slice(0, 500));
                          setPreview(undefined);
                        }
                      }}>Use selection</button>
                    </div>
                    {manual.map((item, index) => <div className="manual-item" key={item}><span>{item}</span>
                      <button type="button" disabled={busy} onClick={() => { setManual(manual.filter((_, i) => i !== index)); setPreview(undefined); }}>Remove</button>
                    </div>)}
                  </form>
              </ReviewWorkspace>
              <div className="finish">
                <p className="muted">Review the full source before release. Candidate detection can miss sensitive information.</p>
                <button disabled={busy || !ready} onClick={() => void previewRedactions()}>Preview redactions</button>
              </div>
              {preview !== undefined && <section ref={previewPanel} className="panel" tabIndex={-1} aria-label="Redaction preview">
                <h2>Redaction preview</h2>
                <pre tabIndex={0}>{preview.text}</pre>
                <div className="exports"><button className="primary" disabled={busy || !ready}
                  onClick={() => void finalize()}>{busy ? "Working…" : "Apply redactions"}</button></div>
              </section>}
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
                    <button className="primary" onClick={() => download(new Blob([artifactBytes(release.txt)], { type: "text/plain;charset=utf-8" }), "sanitized.txt")}>Download TXT</button>
                    {release.pdf && <button onClick={() => download(new Blob([
                      artifactBytes(release.pdf!),
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
