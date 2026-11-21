import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Build a dedicated same-origin bundle regardless of any developer frontend/.env.local.
const cwd = fileURLToPath(new URL("../frontend/", import.meta.url));
const env = { ...process.env, REACT_APP_BACKEND_URL: "/", BUILD_PATH: fileURLToPath(new URL("./dist", import.meta.url)),
  CI: "true", GENERATE_SOURCEMAP: "false" };
execSync("corepack yarn install --frozen-lockfile --production=false && corepack yarn build", { cwd, env, stdio: "inherit" });
