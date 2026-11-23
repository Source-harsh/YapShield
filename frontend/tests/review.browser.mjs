import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const chromium = process.env.PRIVYDOC_CHROME || (process.platform === "win32"
  ? "C:/Program Files/Google/Chrome/Application/chrome.exe" : "chromium");
const python = process.env.PRIVYDOC_PYTHON || join(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const liveOllama = process.env.PRIVYDOC_BROWSER_LIVE_OLLAMA === "1";

async function waitFor(check, message, attempts = 100) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const result = await check();
    if (result) return result;
    await delay(50);
  }
  throw new Error(message);
}

function launch(command, args, cwd, env = process.env) {
  const child = spawn(command, args, { cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", (chunk) => { log += chunk; });
  child.stderr.on("data", (chunk) => { log += chunk; });
  child.on("error", (error) => { log += error.message; });
  return { child, log: () => log };
}

async function pdfFixture(action, first, second) {
  const process = launch(python, ["-B", join(root, "tests/browser_pdf_fixture.py"), action, first, second], root);
  const code = await new Promise((resolve, reject) => {
    process.child.once("close", resolve);
    process.child.once("error", reject);
  });
  assert.equal(code, 0, process.log());
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let id = 0;
  const pending = new Map();
  const listeners = new Map();
  socket.addEventListener("message", ({ data }) => {
    const reply = JSON.parse(data);
    if (reply.method) listeners.get(reply.method)?.(reply.params);
    const request = pending.get(reply.id);
    if (!request) return;
    pending.delete(reply.id);
    if (reply.error) request.reject(new Error(reply.error.message));
    else request.resolve(reply.result);
  });
  return {
    close: () => socket.close(),
    on: (method, listener) => listeners.set(method, listener),
    send(method, params = {}) {
      return new Promise((resolve, reject) => {
        pending.set(++id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

test(liveOllama ? "real Ollama candidate requires browser review before release"
  : "production review UI releases TXT and PDF through the complete workflow", { timeout: 60000 }, async (t) => {
  const scratch = await mkdtemp(join(tmpdir(), "privydoc-browser-"));
  const profile = join(scratch, "chrome");
  const input = join(scratch, "review.txt");
  const text = liveOllama ? "🙂 Luma Fenwick checked the inventory."
    : "🙂\nName: Jane Doe\nJ. Doe filed the report.\nEmail: demo@example.org\nCopy: demo@example.org\nCase: DEMO@example.org\nAccount Number: 202-555-0182";
  await writeFile(input, text, "utf8");
  const backend = launch(python, ["-B", "-m", "uvicorn", "privydoc.api:app", "--host", "127.0.0.1", "--port", "0", "--no-access-log"],
    root, { ...process.env, PRIVYDOC_RULES_ONLY: liveOllama ? "0" : "1", PRIVYDOC_OLLAMA_ENABLED: liveOllama ? "1" : "0" });
  const browser = launch(chromium, ["--headless", "--disable-background-networking", "--no-first-run", "--remote-debugging-port=0",
    "--window-size=1440,1000", `--user-data-dir=${profile}`, "about:blank"], root);
  let page;
  t.after(async () => {
    page?.close();
    browser.child.kill();
    backend.child.kill();
    const directory = await realpath(scratch);
    if (dirname(directory) === await realpath(tmpdir()) && basename(directory).startsWith("privydoc-browser-")) {
      await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
  });
  const origin = await waitFor(() => {
    if (backend.child.exitCode !== null) throw new Error(backend.log());
    return backend.log().match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
  },
    "Local backend did not start. Build the frontend and check PRIVYDOC_PYTHON.").catch((error) => {
      throw new Error(`${error.message}\n${backend.log()}`);
    });
  const port = await waitFor(async () => (await readFile(join(profile, "DevToolsActivePort"), "utf8").catch(() => "")).split("\n")[0],
    "Chromium did not start. Check PRIVYDOC_CHROME.");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  page = await connect(targets.find(({ type }) => type === "page").webSocketDebuggerUrl);
  const networkUrls = new Set();
  let reviewResponse;
  page.on("Network.requestWillBeSent", ({ request }) => networkUrls.add(request.url));
  page.on("Network.responseReceived", ({ response }) => {
    if (response.url === `${origin}/api/review` && response.status !== 204) reviewResponse = response;
  });
  await page.send("Network.enable");
  const evaluate = async (expression) => {
    const reply = await page.send("Runtime.evaluate", { expression, returnByValue: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
    return reply.result.value;
  };
  const waitForUi = (expression) => waitFor(() => evaluate(expression), `Timed out waiting for ${expression}`, liveOllama ? 420 : 100);
  const click = async (selector) => {
    const position = await evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element || element.disabled) return null;
      element.scrollIntoView({block:'center',inline:'center'});
      const rect = element.getClientRects()[0];
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`);
    assert.ok(position, `Missing or disabled control ${selector}`);
    for (const type of ["mousePressed", "mouseReleased"]) {
      await page.send("Input.dispatchMouseEvent", { type, ...position, button: "left", clickCount: 1 });
    }
  };
  const press = async (key) => {
    await page.send("Input.dispatchKeyEvent", { type: "keyDown", key, text: key });
    await page.send("Input.dispatchKeyEvent", { type: "keyUp", key });
  };
  const source = ".source-mention";
  const rows = ".mention-list button";
  const row = (line) => `.mention-list li:nth-child(${line}) button`;
  const current = `document.querySelector('${rows}[aria-current=true]')?.textContent`;
  const selected = (name) => waitForUi(`${current}?.includes(${JSON.stringify(name)})`);
  const preview = "section[aria-label='Redaction preview']";

  await page.send("Page.navigate", { url: origin });
  await waitForUi("!!document.querySelector('input[type=file]')");
  async function upload(file) {
    const { root: documentNode } = await page.send("DOM.getDocument");
    const { nodeId } = await page.send("DOM.querySelector", { nodeId: documentNode.nodeId, selector: "input[type=file]" });
    await page.send("DOM.setFileInputFiles", { nodeId, files: [file] });
  }
  await upload(input);
  if (liveOllama) {
    await waitForUi(`document.querySelector('${source}')?.textContent === 'Luma Fenwick'`);
    assert.equal(reviewResponse.status, 200);
    assert.equal(Object.entries(reviewResponse.headers).find(([key]) => key.toLowerCase() === "x-privydoc-ollama")?.[1], "completed");
    await click(source);
    assert.match(await evaluate("document.querySelector('.mention-detail').textContent"), /Ollama/);
    assert.equal(await evaluate("document.querySelector('.finish button').disabled"), true);
    await press("s");
    await selected("Kept");
    await click(".finish button");
    await waitForUi(`!!document.querySelector("${preview}")`);
    assert.equal(await evaluate(`document.querySelector("${preview} pre").textContent`), text);
    await click(`${rows}[aria-current=true]`);
    await press("r");
    await selected("Approved for redaction");
    await waitForUi(`!document.querySelector("${preview}")`);
    await click(".finish button");
    await waitForUi(`!!document.querySelector("${preview}")`);
    await click(`${preview} button`);
    await waitForUi("document.querySelector('.workspace h2')?.textContent === 'Sanitized document'");
    assert.equal(await evaluate("document.querySelector('.workspace pre').textContent"), "🙂 [REDACTED] checked the inventory.");
    assert.deepEqual([...networkUrls].filter((url) => /^https?:/.test(url) && new URL(url).origin !== origin), []);
    return;
  }
  await waitForUi(`document.querySelectorAll('${rows}').length === 7`);
  assert.equal(await evaluate("document.querySelector('pre').textContent"), text);
  assert.equal(await evaluate(`document.querySelectorAll('${source}')[0].textContent`), "Jane Doe");

  const aliasSelector = `${source}[aria-label*='J. Doe']`;
  await click(aliasSelector);
  await selected("J. Doe");
  assert.equal(await evaluate(`document.activeElement === document.querySelector('${rows}[aria-current=true]')`), true);
  await click(row(1));
  await selected("Jane Doe");
  assert.equal(await evaluate(`document.querySelector("${source}[aria-label*='Jane Doe']").getAttribute('aria-pressed')`), "true");

  await page.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
  await click(".mention-navigation button:nth-child(2)");
  await selected("J. Doe");
  const widths = await evaluate("({ page: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth })");
  assert.ok(widths.page <= widths.viewport, `Narrow review overflows the viewport (${widths.page} > ${widths.viewport})`);
  await click(".mention-navigation button:first-child");
  await selected("Jane Doe");
  await page.send("Emulation.clearDeviceMetricsOverride");

  await press("j");
  await selected("J. Doe");
  await press("r");
  await selected("Approved for redaction");
  await press("s");
  await selected("Kept");
  await click("[aria-label='Identity link decision'] button:nth-child(2)");
  await selected("Link rejected");
  await click("[aria-label='Identity link decision'] button:nth-child(3)");
  await selected("Detached from identity");
  await click("[aria-label='Identity link decision'] button:first-child");
  await selected("Linked identity");
  await selected("Kept");
  await press("k");
  await selected("Jane Doe");

  await evaluate("document.querySelector('textarea').focus()");
  for (const key of ["j", "r", "s", "?"]) await press(key);
  assert.match(await evaluate(current), /Jane Doe.*Detected/);
  assert.equal(await evaluate("document.querySelector('textarea').value"), "jrs?");
  assert.equal(await evaluate("document.querySelector('#review-shortcut-help').hidden"), true);
  await evaluate(`(() => {
    const node = document.createElement('input'); node.id = 'input-test'; document.body.append(node); node.focus();
  })()`);
  await press("j");
  assert.match(await evaluate(current), /Jane Doe/);
  assert.equal(await evaluate("document.querySelector('#input-test').value"), "j");
  await evaluate("document.querySelector('#input-test').remove()");
  await evaluate(`(() => {
    const node = document.createElement('div'); node.contentEditable = 'true'; node.id = 'editable-test';
    node.append(document.createElement('span')); document.body.append(node); node.focus();
  })()`);
  await press("j");
  assert.match(await evaluate(current), /Jane Doe/);
  assert.equal(await evaluate("document.querySelector('#editable-test').textContent"), "j");
  await evaluate("document.querySelector('#editable-test').remove()");
  // Clear the typed literal through the real input event before requesting a preview.
  await evaluate(`(() => {
    const textarea = document.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, '');
    textarea.dispatchEvent(new Event('input', {bubbles:true})); textarea.blur();
  })()`);
  await press("?");
  await waitForUi("!document.querySelector('#review-shortcut-help').hidden");
  await click("#review-shortcut-help input[type=checkbox]");
  await click(row(1));
  await press("j");
  assert.match(await evaluate(current), /Jane Doe/);
  await click("#review-shortcut-help input[type=checkbox]");

  await click(".mention-detail details summary");
  await click(".mention-detail details .controls button:first-child");
  await selected("Approved for redaction");
  assert.match(await evaluate(`document.querySelector('${row(2)}').textContent`), /Kept/);
  await click(row(3));
  await press("s");
  await click(".mention-detail details summary");
  assert.equal(await evaluate("document.querySelectorAll('.mention-detail details .occurrence-list li').length"), 2);
  await click(".mention-detail details .controls button:first-child");
  assert.match(await evaluate(`document.querySelector('${row(3)}').textContent`), /Kept/);
  assert.match(await evaluate(`document.querySelector('${row(4)}').textContent`), /Approved for redaction/);
  assert.match(await evaluate(`document.querySelector('${row(5)}').textContent`), /Detected/);
  await click(row(5));
  await press("s");

  const overlap = `${source}[data-overlap=true]`;
  await click(overlap);
  const firstOverlap = await evaluate(current);
  await press("r");
  await selected("Approved for redaction");
  await click(overlap);
  assert.notEqual(await evaluate(current), firstOverlap);
  await press("s");
  await selected("Kept");
  await click(".finish button");
  await waitForUi("document.querySelector('[role=alert]')?.textContent.includes('overlaps a mention marked Keep')");
  assert.equal(await evaluate(`!!document.querySelector("${preview}")`), false);
  await click(`${rows}[aria-current=true]`);
  await press("r");

  await click(".finish button");
  await waitForUi(`!!document.querySelector("${preview}")`);
  const expected = "🙂\nName: [REDACTED]\nJ. Doe filed the report.\nEmail: demo@example.org\nCopy: [REDACTED]\nCase: DEMO@example.org\nAccount Number: [REDACTED]";
  assert.equal(await evaluate(`document.querySelector("${preview} pre").textContent`), expected);
  await press("s");
  await waitForUi(`!document.querySelector("${preview}")`);
  await press("r");
  await click(".finish button");
  await waitForUi(`!!document.querySelector("${preview}")`);
  await click(`${preview} button`);
  await waitForUi("document.querySelector('.workspace h2')?.textContent === 'Sanitized document'");
  assert.equal(await evaluate("document.querySelector('.workspace pre').textContent"), expected);
  assert.match(await evaluate("document.querySelector('.workspace .panel').textContent"), /verified TXT/);
  assert.equal(await evaluate("Array.from(document.querySelectorAll('.workspace button')).some(button => button.textContent === 'Download PDF')"), false);
  await page.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: scratch });
  await click(".workspace .exports button");
  const downloaded = await waitFor(
    () => readFile(join(scratch, "sanitized.txt"), "utf8").catch(() => ""),
    "The verified TXT download did not complete.",
  );
  assert.equal(downloaded, expected);

  const malformed = join(scratch, "damaged.pdf");
  await writeFile(malformed, "synthetic invalid PDF");
  await click("button.secondary");
  await waitForUi("!!document.querySelector('input[type=file]')");
  await upload(malformed);
  await waitForUi("document.querySelector('[role=alert]')?.textContent.includes('PDF')");
  assert.equal(await evaluate("!!document.querySelector('.mention-list')"), false);

  const pdfTextPath = join(scratch, "pdf-input.txt");
  const pdfPath = join(scratch, "review.pdf");
  const safeSentence = "The review team completed the audit and approved the public report.";
  const address = "Flat B-12, Sector 17, Chandigarh 160017";
  const pdfText = [
    "Name: Élodie D’Arcy",
    "E. D’Arcy filed it.",
    "Élodie checked it.",
    "Dr D’Arcy signed it.",
    "Name: Elodie Darcy",
    "Student ID: IITM_23CS104",
    "Employee ID: IN-HR-04572",
    `Address: ${address}`,
    "Phone: +91 98765 43210",
    "Copy: +91 98765 43210",
    "Email: public@example.org",
    "Copy: public@example.org",
    safeSentence,
  ].join("\n");
  await writeFile(pdfTextPath, pdfText, "utf8");
  await pdfFixture("create", pdfTextPath, pdfPath);

  // Observe the actual requests and responses while forwarding them unchanged.
  await evaluate(`(() => {
    window.reviewCalls = [];
    const fetchOriginal = window.fetch;
    window.fetch = async (url, options) => {
      const response = await fetchOriginal(url, options);
      if (String(url).startsWith('/api/')) {
        window.reviewCalls.push({
          path: url, status: response.status, headers: options.headers,
          body: typeof options.body === 'string' ? JSON.parse(options.body) : undefined,
          response: response.status === 204 ? undefined : await response.clone().json(),
        });
      }
      return response;
    };
  })()`);
  await upload(pdfPath);
  await waitForUi(`document.querySelector('pre')?.textContent.includes(${JSON.stringify(safeSentence)})`);
  assert.equal((await evaluate("document.querySelector('pre').textContent")).trim(), pdfText);
  const mentions = await evaluate(
    `Array.from(document.querySelectorAll('${rows}'), button => ({ id: button.id, text: button.firstElementChild.textContent }))`,
  );
  const textFor = (value) => mentions.find(({ text }) => text === value);
  const choose = async (mention) => {
    assert.ok(mention, "Expected mention is missing");
    await click(`[id="${mention.id}"]`);
  };
  const decide = async (mention, key) => {
    await choose(mention);
    await press(key);
  };
  await choose(textFor("Élodie D’Arcy"));
  assert.match(await evaluate("document.querySelector('.mention-detail').textContent"), /Context rule/);

  const aliases = [["E. D’Arcy", 1, "r"], ["Élodie", 2, "s"], ["Dr D’Arcy", 3, "r"]];
  const aliasValues = aliases.map(([value]) => value);
  const phones = mentions.filter(({ text }) => text === "+91 98765 43210");
  const emails = mentions.filter(({ text }) => text === "public@example.org");
  assert.equal(phones.length, 2);
  assert.equal(emails.length, 2);
  for (const mention of mentions.filter(({ text }) => ![...aliasValues, phones[0].text, emails[0].text].includes(text))) {
    await decide(mention, "r");
  }
  for (const [value, link, key] of aliases) {
    await decide(textFor(value), key);
    await click(`[aria-label='Identity link decision'] button:nth-child(${link})`);
  }
  await choose(phones[0]);
  await click(".mention-detail details summary");
  await click(".mention-detail details .controls button:first-child");
  await decide(emails[0], "s");
  await decide(emails[1], "r");
  await evaluate(`(() => {
    const field = document.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(field, ${JSON.stringify(address)});
    field.dispatchEvent(new Event('input', {bubbles:true}));
  })()`);
  await click(".manual .controls button:first-of-type");

  await click(".finish button");
  await waitForUi(`!!document.querySelector("${preview}")`);
  const pdfPreview = await evaluate(`document.querySelector("${preview} pre").textContent`);
  const expectedPdf = [
    "Name: [REDACTED]", "[REDACTED] filed it.", "Élodie checked it.", "[REDACTED] signed it.", "Name: [REDACTED]",
    "Student ID: [REDACTED]", "Employee ID: [REDACTED]", "Address: [REDACTED]", "Phone: [REDACTED]",
    "Copy: [REDACTED]", "Email: public@example.org", "Copy: [REDACTED]", safeSentence,
  ].join("\n");
  assert.equal(pdfPreview.trim(), expectedPdf);
  await click(`${preview} button`);
  await waitForUi("document.querySelector('.workspace h2')?.textContent === 'Sanitized document'");
  const calls = await evaluate("window.reviewCalls");
  const previewCall = calls.find(({ path }) => path === "/api/preview");
  const releaseCall = calls.find(({ path }) => path === "/api/release");
  assert.equal(releaseCall.status, 200);
  assert.deepEqual(releaseCall.body, previewCall.body);
  assert.equal(releaseCall.headers["X-Preview-Receipt"], previewCall.response.receipt);
  assert.deepEqual(releaseCall.body.manual, [address]);
  assert.deepEqual(Object.values(releaseCall.body.links).map(({ status }) => status).sort(), ["confirm", "detach", "reject"]);
  assert.equal(releaseCall.response.text, pdfPreview);
  assert.match(releaseCall.response.summary, /approved the public report/);
  assert.doesNotMatch(releaseCall.response.summary, /Élodie|Darcy|D’Arcy|example\.org|Chandigarh|SYNTHETIC_HIDDEN/);
  const downloads = join(scratch, "pdf-downloads");
  await page.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
  for (const [selector, filename] of [
    [".workspace .panel:first-child .exports button:first-child", "sanitized.txt"],
    [".workspace .panel:first-child .exports button:nth-child(2)", "sanitized.pdf"],
    [".workspace .panel:nth-child(2) .exports button", "summary.txt"],
  ]) {
    await click(selector);
    const content = await waitFor(() => readFile(join(downloads, filename)).catch(() => null), `Missing ${filename}`);
    const field = { "sanitized.txt": "txt", "sanitized.pdf": "pdf", "summary.txt": "summary" }[filename];
    assert.deepEqual(content, Buffer.from(releaseCall.response[field], field === "summary" ? "utf8" : "base64"));
  }
  await pdfFixture("check", join(downloads, "sanitized.pdf"), join(downloads, "sanitized.txt"));

  const leakageInput = join(scratch, "summary-privacy.txt");
  const hiddenEmail = "demo＠example.org";
  await writeFile(leakageInput, `Internal reference: ZX99\nPlease notify the entire support group through ${hiddenEmail} immediately.`, "utf8");
  await click("button.secondary");
  await waitForUi("!!document.querySelector('input[type=file]')");
  await upload(leakageInput);
  await waitForUi("document.querySelector('pre')?.textContent.includes('ZX99')");
  await click("textarea");
  for (const key of "ZX99") await press(key);
  await click(".manual .controls button:first-of-type");
  await click(".finish button");
  await waitForUi(`!!document.querySelector("${preview}")`);
  await click(`${preview} button`);
  await waitForUi("document.querySelector('.workspace h2')?.textContent === 'Sanitized document'");
  assert.match(await evaluate("document.querySelector('.workspace pre').textContent"), /\[REDACTED\]/);
  assert.ok((await evaluate("document.querySelector('.workspace pre').textContent")).includes(hiddenEmail));
  assert.match(await evaluate("document.querySelector('.workspace .panel:nth-child(2)').textContent"), /No summary is available/);
  assert.equal(await evaluate("!!document.querySelector('.workspace .panel:nth-child(2) .exports button')"), false);
  assert.deepEqual([...networkUrls].filter((url) => /^https?:/.test(url) && new URL(url).origin !== origin), []);
});
