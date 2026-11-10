/**
 * HarnessRouter from TypeScript with the official `openai` npm package.
 *
 *   npm install
 *   npx tsx quickstart.ts                 # uses HR_PROVIDER from ../../.env
 *   npx tsx quickstart.ts anthropic       # or openai | openrouter
 *
 * Shows: one task, streaming the next turn of the same session, and reading the stored result.
 * Run this in your BACKEND only. The HarnessRouter key must never reach a browser.
 */
import { config } from "dotenv";
import OpenAI from "openai";

config({ path: new URL("../../.env", import.meta.url).pathname }); // the repo-root .env

const PROFILES = {
  openai: { harness: process.env.HR_OPENAI_HARNESS ?? "codex", model: process.env.HR_OPENAI_MODEL ?? "gpt-5.4-mini" },
  anthropic: { harness: process.env.HR_ANTHROPIC_HARNESS ?? "claude-code", model: process.env.HR_ANTHROPIC_MODEL ?? "claude-sonnet-5" },
  openrouter: { harness: process.env.HR_OPENROUTER_HARNESS ?? "hermes", model: process.env.HR_OPENROUTER_MODEL ?? "claude-sonnet-4.6" },
} as const;

type Provider = keyof typeof PROFILES;
const provider = (process.argv[2] ?? process.env.HR_PROVIDER ?? "openai") as Provider;
const profile = PROFILES[provider];
if (!profile) throw new Error(`unknown provider ${provider}; use ${Object.keys(PROFILES).join(" | ")}`);

const baseURL = (process.env.HARNESSROUTER_BASE_URL ?? "http://localhost:3000/api/harness").replace(/\/$/, "");
const apiKey = process.env.HARNESSROUTER_API_KEY;
if (!apiKey) throw new Error("HARNESSROUTER_API_KEY is not set (see ../../.env.example)");

// Pass baseURL explicitly so OPENAI_BASE_URL in your shell is never used by accident.
const client = new OpenAI({ baseURL: `${baseURL}/v1`, apiKey, timeout: 3_700_000, maxRetries: 0 });

// Join the assistant text of a response. (The SDK's output_text is only filled in by create().)
function text(resp: OpenAI.Responses.Response): string {
  return resp.output
    .flatMap((item) => (item.type === "message" ? item.content : []))
    .map((part) => (part.type === "output_text" ? part.text : ""))
    .join("");
}

async function main() {
  console.log(`provider=${provider} harness_id=${profile.harness} model=${profile.model}\n`);

  // 1. One task, wait for the result.
  const first = await client.responses.create({
    model: profile.model,
    input: "The codeword is PELICAN. Create notes.md with the line 'hello'. Reply with just: saved.",
    metadata: { harness_id: profile.harness },
  });
  const sessionId = (first.metadata as Record<string, string> | null)?.session_id;
  console.log("turn 1:", first.output_text, `| status=${first.status} session=${sessionId}`);

  // 2. Continue the same session and stream it.
  const stream = await client.responses.create({
    model: profile.model,
    input: "What was the codeword?",
    previous_response_id: first.id,
    metadata: { harness_id: profile.harness },
    stream: true,
  });

  let finalId = "";
  process.stdout.write("turn 2: ");
  for await (const event of stream) {
    switch (event.type) {
      case "response.output_text.delta":
        process.stdout.write(event.delta);
        break;
      case "response.output_item.added":
        if (event.item.type === "function_call") process.stdout.write(`\n[tool] ${event.item.name}\n`);
        break;
      case "response.completed":
      case "response.failed":
      case "response.incomplete":
        // The terminal event carries the whole response. status is the truth ("cancelled" arrives as failed).
        finalId = event.response.id;
        console.log(`\n[${event.type}] status=${event.response.status}`);
        break;
    }
  }

  // 3. The stored response is the source of truth, even if the stream dropped.
  const stored = await client.responses.retrieve(finalId);
  console.log("stored:", stored.status, JSON.stringify(text(stored)));
}

main().catch((e) => {
  console.error(e?.error ?? e);
  process.exit(1);
});
