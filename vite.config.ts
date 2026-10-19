import { defineConfig, type Plugin } from "vite";
import vue from "@vitejs/plugin-vue";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

// Development-only local bridge; packaged applications use the Tauri command.
function localCore(): Plugin {
  return {
    name: "local-mmtr-core",
    configureServer(server) {
      const child = spawn("python", ["-u", "-m", "mmtr_editor.rpc"], {
        cwd: process.cwd(),
        windowsHide: true,
      });
      const pending: Array<{
        resolve: (v: string) => void;
        reject: (e: Error) => void;
      }> = [];
      const lines = createInterface({ input: child.stdout });
      lines.on("line", (line) => pending.shift()?.resolve(line));
      child.stderr.on("data", (data) => process.stderr.write(data));
      child.on("error", (error) => {
        while (pending.length) pending.shift()?.reject(error);
      });
      child.on("exit", () => {
        while (pending.length)
          pending.shift()?.reject(new Error("MMTR core exited"));
      });
      server.httpServer?.once("close", () => child.kill());
      server.middlewares.use("/__mmtr__/rpc", (req, res) => {
        const origin = req.headers.origin;
        const host = req.headers.host;
        if (
          req.method !== "POST" ||
          !["127.0.0.1:5173", "localhost:5173"].includes(host ?? "") ||
          (origin &&
            !["http://127.0.0.1:5173", "http://localhost:5173"].includes(
              origin,
            ))
        ) {
          res.writeHead(403);
          res.end();
          return;
        }
        let body = "";
        req.on("data", (chunk) => {
          body += chunk;
          if (body.length > 16_000_000) req.destroy();
        });
        req.on("end", async () => {
          try {
            const payload = JSON.parse(body);
            const result = new Promise<string>((resolve, reject) =>
              pending.push({ resolve, reject }),
            );
            child.stdin.write(JSON.stringify(payload) + "\n");
            const output = await result;
            res.setHeader("Content-Type", "application/json; charset=utf-8");
            res.end(output);
          } catch (error) {
            res.writeHead(500);
            res.end(JSON.stringify({ ok: false, error: String(error) }));
          }
        });
      });
    },
  };
}
export default defineConfig({
  plugins: [vue(), localCore()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    watch: {
      ignored: [
        "**/src-tauri/**",
        "**/.build/**",
        "**/output/**",
        "**/release/**",
      ],
    },
  },
  build: { target: "es2022" },
});
