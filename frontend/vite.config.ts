import { defineConfig } from "vite";
import basicSsl from "@vitejs/plugin-basic-ssl";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      // Standalone voice (STT) service. It owns its own root paths
      // (/transcribe, /health), so the /voice mount prefix is stripped before
      // forwarding. Change target to move STT to another machine.
      "/voice": {
        target: "http://127.0.0.1:8100",
        rewrite: (path) => path.replace(/^\/voice/, ""),
      },
      // Standalone TTS (text-to-speech) service — same pattern as /voice.
      // 8200 melo / 8201 piper; both speak the same contract (tts/README).
      "/tts": {
        target: "http://127.0.0.1:8201",
        rewrite: (path) => path.replace(/^\/tts/, ""),
      },
    },
  },
});
