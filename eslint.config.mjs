/*
 * ESLint for the extension, the dashboard and the test scripts.
 *
 *   npm ci && npm run lint
 *
 * Nothing here is built or bundled: each file is a plain browser script, an
 * extension script or a Node test, and the rules below say which is which.
 * CI runs this on every push (.github/workflows/selftest.yml).
 */
import js from "@eslint/js";
import globals from "globals";

// dashboard.js defines these at the top level of the page, and arbeitnow.js
// and searches.js, loaded after it on the same page, use them.
const DASHBOARD_SHARED = ["$", "esc", "post", "toast", "tick", "loadEvents", "dlgTabs"];

const unused = { caughtErrors: "none", ignoreRestSiblings: true };

export default [
  { ignores: ["node_modules/", "**/.venv/", "**/out/", "_backups/", "profiles/", "applications/"] },
  js.configs.recommended,
  {
    rules: {
      // `catch (e) {}` with nothing to do is deliberate throughout: a failed
      // read or a closed tab is not an error worth reporting.
      "no-empty": ["error", { allowEmptyCatch: true }],
      // `({ _el, ...rest })` is how a field is dropped from a copy.
      "no-unused-vars": ["error", unused],
    },
  },
  {
    files: ["**/*.js"],
    languageOptions: { ecmaVersion: 2024, sourceType: "script", globals: globals.browser },
  },
  {
    files: ["extension/**/*.js"],
    languageOptions: { globals: globals.webextensions },
  },
  {
    files: ["web/dashboard.js"],
    rules: { "no-unused-vars": ["error", { ...unused, varsIgnorePattern: "^dlgTabs$" }] },
  },
  {
    files: ["web/arbeitnow.js", "web/searches.js"],
    languageOptions: {
      globals: Object.fromEntries(DASHBOARD_SHARED.map((g) => [g, "readonly"])),
    },
  },
  {
    // The selftest loads its URL helpers in Node through module.exports.
    files: ["web/searches.js"],
    languageOptions: { globals: { module: "readonly" } },
  },
  {
    files: ["**/*.mjs"],
    languageOptions: { ecmaVersion: 2024, sourceType: "module", globals: globals.node },
  },
];
