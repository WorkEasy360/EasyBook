import coreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * Flat config. eslint-config-next 16 ships native flat-config arrays, so no
 * FlatCompat shim is needed — and with ESLint 10 the shim actually throws
 * while validating the nested plugin objects.
 */
const config = [
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
  ...coreWebVitals,
  ...nextTypescript,
  {
    rules: {
      // Root CLAUDE.md: typed API contracts, no `any` for response shapes.
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": [
        "error",
        { prefer: "type-imports", fixStyle: "inline-type-imports" },
      ],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    /*
     * Money must never be computed with JS numbers (root CLAUDE.md rule 3).
     * src/lib/money.ts is the one module allowed near a primitive conversion,
     * and even there it only formats decimal STRINGS.
     */
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/lib/money.ts", "src/lib/money.test.ts"],
    rules: {
      "no-restricted-globals": [
        "error",
        {
          name: "parseFloat",
          message: "Money must go through src/lib/money.ts (Big), never a float.",
        },
      ],
      "no-restricted-properties": [
        "error",
        {
          object: "Number",
          property: "parseFloat",
          message: "Money must go through src/lib/money.ts (Big), never a float.",
        },
      ],
    },
  },
  {
    // Playwright specs run in Node against a real browser, not in the app.
    files: ["e2e/**/*.ts", "playwright.config.ts"],
    rules: {
      "no-console": "off",
    },
  },
];

export default config;
