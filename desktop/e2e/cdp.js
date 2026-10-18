// A very small Chrome DevTools Protocol client, enough to look at and drive the
// window of the real app from outside (Electron is started with
// --remote-debugging-port by e2e/app.js). Node 22 has WebSocket built in.

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function listTargets(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json`, {
    headers: { Connection: "close" }, signal: AbortSignal.timeout(3000),
  });
  return response.json();
}

// The first page of the app whose address starts with `prefix`.
async function waitForPage(port, prefix, timeoutMs = 120_000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try {
      const page = (await listTargets(port)).find((t) => t.type === "page" && t.url.startsWith(prefix));
      if (page) return page;
    } catch {
      /* not listening yet */
    }
    await sleep(250);
  }
  throw new Error(`no page starting with ${prefix} within ${timeoutMs} ms`);
}

async function attach(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let counter = 0;
  const waiting = new Map();
  socket.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    if (data.id && waiting.has(data.id)) {
      waiting.get(data.id)(data);
      waiting.delete(data.id);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++counter;
      waiting.set(id, (reply) => (reply.error ? reject(new Error(`${method}: ${reply.error.message}`)) : resolve(reply.result)));
      socket.send(JSON.stringify({ id, method, params }));
    });

  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? "evaluation failed");
    return result.result.value;
  };

  async function waitFor(expression, label, timeoutMs = 60_000) {
    const end = Date.now() + timeoutMs;
    let last;
    while (Date.now() < end) {
      try {
        last = await evaluate(expression);
        if (last) return last;
      } catch {
        /* the page may be reloading */
      }
      await sleep(250);
    }
    const seen = await evaluate("document.body ? document.body.innerText.slice(0, 600) : ''").catch(() => "(page not readable)");
    throw new Error(`timed out waiting for ${label}. The page showed: ${JSON.stringify(seen)}`);
  }

  return {
    evaluate,
    waitFor,
    send,
    text: () => evaluate("document.body ? document.body.innerText : ''"),
    screenshot: async () => Buffer.from((await send("Page.captureScreenshot", { format: "png" })).data, "base64"),
    close: () => socket.close(),
  };
}

module.exports = { listTargets, waitForPage, attach, sleep };
