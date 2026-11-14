import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));

export function recommendationsPlugin(loadServerEnv) {
  const attach = (server) => {
    server.middlewares.use("/api/recommendations", async (req, res) => {
      const reply = (status, payload) => {
        if (res.destroyed || res.headersSent) return;
        res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        res.end(JSON.stringify(payload));
      };
      if (req.method !== "POST") return reply(405, { error: "Use POST to generate a report." });
      if (req.headers.origin && ![`http://${req.headers.host}`, `https://${req.headers.host}`].includes(req.headers.origin)) {
        return reply(403, { error: "Open recommendations from the Steward Lessons page." });
      }
      if (req.headers["content-type"] !== "application/json") return reply(415, { error: "JSON input is required." });
      const chunks = [];
      let size = 0;
      try {
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 8192) return reply(413, { error: "The request is too large." });
          chunks.push(chunk);
        }
      } catch { return; }
      const child = spawn("uv", ["run", "--no-sync", "python", "-m", "steward.recommendations"], {
        cwd: root, env: { ...process.env, ...loadServerEnv() }, timeout: 90000,
        stdio: ["pipe", "pipe", "inherit"],
      });
      let output = "";
      child.stdout.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
      child.stdin.on("error", () => {});
      child.on("error", () => reply(503, { error: "Install uv and run uv sync to enable recommendations." }));
      child.on("close", (code) => {
        if (code !== 0) return reply(503, { error: "The report did not finish. Please try again." });
        try { reply(200, JSON.parse(output)); }
        catch { reply(503, { error: "The report could not be read. Please try again." }); }
      });
      res.on("close", () => { if (child.exitCode === null && !child.killed) child.kill(); });
      child.stdin.end(Buffer.concat(chunks));
    });
  };
  return { name: "steward-recommendations", configureServer: attach, configurePreviewServer: attach };
}
