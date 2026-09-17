import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library does not auto-clean under Vitest's globals, and a
// leaked DOM between tests produces "found multiple elements" failures that
// look like component bugs.
afterEach(() => {
  cleanup();
});
