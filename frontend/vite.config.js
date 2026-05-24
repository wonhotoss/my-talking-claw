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
        },
    },
});
