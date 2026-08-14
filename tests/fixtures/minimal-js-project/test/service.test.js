import assert from "node:assert/strict";
import test from "node:test";

import { result } from "../src/service.js";

test("returns the public result shape", () => {
  assert.deepEqual(result(), { value: 1 });
});
