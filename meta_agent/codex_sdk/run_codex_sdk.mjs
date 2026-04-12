import { Codex } from "@openai/codex-sdk";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parseInput(raw) {
  if (!raw.trim()) {
    throw new Error("No JSON payload provided on stdin");
  }
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Invalid payload: expected JSON object");
  }
  if (!parsed.prompt || typeof parsed.prompt !== "string") {
    throw new Error("Invalid payload: 'prompt' must be a non-empty string");
  }
  if (!parsed.workingDirectory || typeof parsed.workingDirectory !== "string") {
    throw new Error("Invalid payload: 'workingDirectory' must be a string");
  }
  return parsed;
}

function withTimeout(promise, timeoutMs) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return promise;
  }
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(
        () =>
          reject(new Error(`TIMEOUT after ${Math.floor(timeoutMs / 1000)}s`)),
        timeoutMs,
      );
    }),
  ]);
}

function stringifyError(err) {
  if (err instanceof Error) {
    return err.stack || err.message;
  }
  return String(err);
}

try {
  const input = parseInput(await readStdin());
  const prompt = input.prompt;
  const workingDirectory = input.workingDirectory;
  const model = typeof input.model === "string" ? input.model : "";
  const skipGitRepoCheck = input.skipGitRepoCheck !== false;
  const timeoutSec = Number.isFinite(input.timeoutSec)
    ? Number(input.timeoutSec)
    : 300;

  const codexApiKey = (
    typeof process.env.CODEX_API_KEY === "string" &&
    process.env.CODEX_API_KEY.trim()
      ? process.env.CODEX_API_KEY
      : typeof process.env.OPENAI_API_KEY === "string"
        ? process.env.OPENAI_API_KEY
        : ""
  ).trim();
  const codexConfig = model ? { model } : {};
  const codex = new Codex({
    apiKey: codexApiKey || undefined,
    config: codexConfig,
  });
  const thread = codex.startThread({
    workingDirectory,
    skipGitRepoCheck,
    sandboxMode: "workspace-write",
    approvalPolicy: "never",
  });

  const turn = await withTimeout(thread.run(prompt), timeoutSec * 1000);

  const output = {
    ok: true,
    finalResponse:
      typeof turn.finalResponse === "string" ? turn.finalResponse : "",
    items: Array.isArray(turn.items) ? turn.items : [],
  };
  process.stdout.write(JSON.stringify(output));
} catch (err) {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      error: stringifyError(err),
    }),
  );
  process.exit(1);
}
