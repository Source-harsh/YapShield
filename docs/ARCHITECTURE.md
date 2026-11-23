# PrivyDoc architecture

PrivyDoc is a local-first document review and release tool for Thilli AI Hackathon, Problem Statement 3. React and TypeScript run in the browser. A single FastAPI process on loopback serves the production build and handles document processing with PyMuPDF and optional spaCy. Vite proxies API calls to the same backend during development.

```mermaid
flowchart TD
    U[User] --> F[React frontend]
    F --> B[FastAPI backend]
    B --> X[Document extraction]
    X --> D[Deterministic rules and optional local spaCy NER]
    D --> M[Candidate merging]
    M --> I[Deterministic identity resolution]
    I --> OQ[Optional local Qwen person suggestions]
    OQ --> R[Human review]
    R --> P[Redaction preview and receipt]
    P --> E[Verified TXT and PDF export]
    E --> S[Extractive summarization of sanitized text]
    S --> V[Summary provenance and leakage validation]
    V --> O[Download verified artifacts and available summary]
```

| Component | Responsibility |
| --- | --- |
| `frontend/src/main.tsx` and `ReviewWorkspace.tsx` | Upload, source highlights, evidence, mention and link decisions, manual selections, preview, artifact downloads |
| `frontend/src/review.ts` | Unicode code-point segmentation, exact-occurrence actions, keyboard handling |
| `privydoc/api.py` | Bounded requests, review lifecycle, complete decision validation, preview receipts, release coordination |
| `privydoc/documents.py` | Extraction, span replacement, independent TXT/PDF verification, extractive ranking |
| `privydoc/detection.py` | Deterministic regex and context rules, optional spaCy NER, same-type exact-span evidence merging |
| `privydoc/identity.py` | Deterministic document-local identity proposals and separate treatment of ambiguous references |
| `privydoc/ollama_detection.py` | Optional upload-only Qwen calls on loopback, validation, and source grounding for standalone person suggestions |
| `privydoc/summary_privacy.py` | Summary provenance, known values, direct identifier patterns, and quasi-identifier checks |

The identity resolver uses compatible name forms, nearby context, and competing anchors to propose links. spaCy supplies NER spans and evidence. Optional Qwen suggestions enter review after baseline linking. They cannot create or change identity links. A user resolves links and decides whether to redact each mention. Summarization ranks and selects excerpts from verified sanitized text using local code.

See [data flow](DATA_FLOW.md), [security assumptions](SECURITY.md), and [evaluation](EVALUATION.md).
