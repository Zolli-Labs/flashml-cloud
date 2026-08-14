// Global test setup.
//
// `lib/api-cache.ts` holds module-level state on purpose — a cache scoped to
// anything narrower than the module could not dedupe across the independent
// components that share the console shell. Module state survives between
// test files in one worker, so without this reset a suite that asserts
// "`listMachines` issued exactly one fetch" would pass or fail depending on
// whether an EARLIER test file had already read `/v1alpha1/machines` inside
// the 1.5s TTL. Reset it before every test rather than asking each suite to
// remember.
import { beforeEach } from "vitest";

import { resetApiCache } from "./lib/api-cache";

beforeEach(() => {
  resetApiCache();
});
