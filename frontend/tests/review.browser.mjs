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

async function waitFor(check, message) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = await check();
    if (result) return result;
    await delay(50);
  }
  throw new Error(message);
}

function launch(command, args, cwd) {
  const child = spawn(command, args, { cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", (chunk) => { log += chunk; });
  child.stderr.on("data", (chunk) => { log += chunk; });
  child.on("error", (error) => { log += error.message; });
  return { child, log: () => log };
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const reply = JSON.parse(data);
    const request = pending.get(reply.id);
    if (!request) return;
    pending.delete(reply.id);
    if (reply.error) request.reject(new Error(reply.error.message));
    else request.resolve(reply.result);
  });
  return {
    close: () => socket.close(),
    send(method, params = {}) {
      return new Promise((resolve, reject) => {
        pending.set(++id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

test("production review UI selects, navigates, previews, and releases precise mentions", { timeout: 45000 }, async (t) => {
  const scratch = await mkdtemp(join(tmpdir(), "privydoc-browser-"));
  const profile = join(scratch, "chrome");
  const input = join(scratch, "review.txt");
  const text = "🙂\nName: Jane Doe\nJ. Doe filed the report.\nEmail: demo@example.org\nCopy: demo@example.org\nCase: DEMO@example.org\nAccount Number: 202-555-0182";
  await writeFile(input, text, "utf8");
  const backend = launch(python, ["-B", "-m", "uvicorn", "privydoc.api:app", "--host", "127.0.0.1", "--port", "0", "--no-access-log"], root);
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
  const evaluate = async (expression) => {
    const reply = await page.send("Runtime.evaluate", { expression, returnByValue: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
    return reply.result.value;
  };
  const waitForUi = (expression) => waitFor(() => evaluate(expression), `Timed out waiting for ${expression}`);
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
  const { root: documentNode } = await page.send("DOM.getDocument");
  const { nodeId } = await page.send("DOM.querySelector", { nodeId: documentNode.nodeId, selector: "input[type=file]" });
  await page.send("DOM.setFileInputFiles", { nodeId, files: [input] });
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
  await click(overlap);
  assert.notEqual(await evaluate(current), firstOverlap);
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
});
