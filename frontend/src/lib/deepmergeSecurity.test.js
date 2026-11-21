const merge = require("deepmerge");

function ownPrototype(value) {
  const object = {};
  Object.defineProperty(object, "__proto__", {
    value, enumerable: true, configurable: true, writable: true,
  });
  return object;
}

test("patched build dependency preserves merge, clone and option behavior", () => {
  const symbol = Symbol("metadata");
  const date = new Date("2026-11-09T00:00:00Z");
  const target = { nested: { first: true }, list: [{ value: 1 }], date, [symbol]: "kept" };
  const source = { nested: { second: true }, list: [{ value: 2 }] };
  const result = merge(target, source);
  expect(result.nested).toEqual({ first: true, second: true });
  expect(result.list).toEqual([{ value: 1 }, { value: 2 }]);
  expect(result.list[0]).not.toBe(target.list[0]);
  expect(result.date).toBe(date);
  expect(result[symbol]).toBe("kept");
  expect(target).not.toHaveProperty("nested.second");
  expect(merge.all([{ first: 1 }, { second: 2 }])).toEqual({ first: 1, second: 2 });
  expect(merge({ list: [1] }, { list: [2] }, { arrayMerge: (_, value) => value }).list).toEqual([2]);
  expect(merge({ label: { value: "one" } }, { label: { value: "two" } }, {
    customMerge: key => key === "label" ? (first, second) => ({ value: first.value + second.value }) : undefined,
  }).label).toEqual({ value: "onetwo" });
});

test("prototype-shaped data stays on an ordinary object across each copy branch", () => {
  const value = { unexpected: "data" };
  const input = ownPrototype(value);
  const shapes = [
    merge(Object.create(null), input),
    merge(input, {}),
    merge({ nested: null }, { nested: input }).nested,
    merge({ nested: undefined }, { nested: input }).nested,
    merge({ nested: 1 }, { nested: input }).nested,
    merge({ nested: input }, { nested: ownPrototype({ next: true }) }).nested,
  ];
  for (const result of shapes) {
    expect(Object.getPrototypeOf(result)).toBe(Object.prototype);
    expect(result.unexpected).toBeUndefined();
    expect(Object.getOwnPropertyDescriptor(result, "__proto__")).toEqual({
      value: expect.any(Object), enumerable: true, writable: true, configurable: true,
    });
  }
  const cloned = merge({}, { list: [input] }).list[0];
  expect(Object.getPrototypeOf(cloned)).toBe(Object.prototype);
  expect(cloned.unexpected).toBeUndefined();
  expect({}.unexpected).toBeUndefined();
});
