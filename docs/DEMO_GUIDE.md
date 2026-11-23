# Hackathon demo

Allow about three minutes after setup. Both fixtures depict the same fictional handoff. The email domains are reserved for examples, the phone number uses the fictional 555-01xx range, and the PDF contains a synthetic metadata marker.

## Start

From the repository root in PowerShell, with the [README setup](../README.md#run-on-windows) completed:

```powershell
npm.cmd --prefix frontend run build
.venv\Scripts\python.exe -m uvicorn privydoc.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000`. FastAPI serves the built frontend. For a separate development frontend, keep the backend running and run `npm.cmd --prefix frontend run dev` in a second terminal, then open the printed URL.

## Live walkthrough

1. Upload [equipment-handoff.pdf](../demo/fixtures/equipment-handoff.pdf). Use [equipment-handoff.txt](../demo/fixtures/equipment-handoff.txt) to repeat the same demo with TXT input.
2. Click the highlighted **T. Vale** mention. Show the source span, line, and evidence. **Confirm link** and **Approve selected mention**. On **Tessa**, show that you can **Reject link** and still approve that mention for redaction separately. Point out the standalone **Vale** ambiguous name and the two independently reviewable **Rina Vale** mentions.
3. Approve the remaining private names, phone, employee ID, and address. For `records@example.org`, expand **Review exact occurrences** and approve both pending occurrences. Mark the fictional public contact `public-desk@example.org` **Keep**. Use `J` and `K` to navigate, `R` to approve, and `S` to Keep.
4. Add `ZX-491` under **Manual redactions**. Resolve any additional candidates or proposals shown in the installed detection mode. Preview and point out the public contact and redaction markers. If time allows, change a decision and show that the release button disappears until you preview again.
5. Select **Apply redactions** and download TXT, PDF, and the summary. Open the exported PDF and show that the private values are removed, the public contact remains, and the content is reflowed. The summary should discuss the equipment handoff and omit the explicitly kept public contact because the summary path masks Keeps.

For a short failure demo, add the public contact as a manual redaction while it is marked Keep. Preview displays the conflict. Remove that entry and preview again.
