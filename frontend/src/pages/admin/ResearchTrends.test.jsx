import { act } from "react";
import { createRoot } from "react-dom/client";
import ResearchTrends, { comparisonCsv } from "./ResearchTrends";
jest.mock("./ResearchNoticeSummary", () => () => null);

const age = "age_band", day = "e:s1", first = "i:conversations:0", second = "i:conversations:1";
const conditions = ["With more trust", "With a clear plan", "In private", "In fantasy only"];
const variables = [
  { id: age, label: "Age band", domain: "demographics" },
  { id: "d:education", label: "Education", domain: "demographics" },
  { id: day, label: "How do you approach a conversation?", domain: "everyday" },
  { id: first, label: "Talk in advance?", domain: "intimacy" },
  { id: second, label: "Leave a note?", domain: "intimacy" },
  { id: "c:prompt:communication-1", label: "How would you begin?", domain: "couples" },
];
function payload(params = {}, available = false) {
  return { x: age, y: first, x_choices: ["18-24", "25-34"], y_choices: ["Curious", "It depends"],
    ...params, conditions, variables, available, minimum: 50, minimum_cell: 10, round_to: 5,
    sample_size: available ? 80 : null, rows: available ? [
      { index: 0, cells: [30, 10], total: 40 }, { index: 1, cells: [10, 30], total: 40 },
    ] : [], columns: available ? [0, 1] : [], cramers_v: available ? 0.5 : null };
}
async function mounted(api, test) {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<ResearchTrends api={api} />));
    await test(host);
  } finally {
    act(() => root.unmount());
    host.remove();
  }
}
function button(host, text) {
  return [...host.querySelectorAll("button")].find(node => node.textContent === text);
}

test("switching research tools aborts the previous read and returning fetches current results", async () => {
  const api={get:jest.fn(() => new Promise(() => {}))};
  await mounted(api, async host => {
    expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/admin/research/trends"]);
    const signal=api.get.mock.calls[0][1].signal;
    await act(async () => button(host,"Conversation tone").click());
    expect(signal.aborted).toBe(true);
    expect(host.textContent).toContain("Conversation tone research");
    expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => button(host,"Questionnaire comparisons").click());
    expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/admin/research/trends","/admin/research/trends"]);
    expect(api.get.mock.calls[1][1].signal).not.toBe(signal);
  });
});
function select(host, text) {
  return [...host.querySelectorAll("label")].find(label =>
    [...label.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join("") === text
  ).querySelector("select");
}
async function change(node, value) {
  await act(async () => { node.value = value; node.dispatchEvent(new Event("change", { bubbles: true })); });
}

test("filtering the catalog keeps the selected comparison and its conditions until the analyst chooses a result", async () => {
  const api = { get: jest.fn(async (_, {params}) => ({data: payload(params)})) };
  await mounted(api, async host => {
    const rows = select(host, "Rows");
    const input = host.querySelector('[aria-label="Filter rows by name or ID"]');
    const type = async query => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, query);
      input.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await type("s1 everyday");
    expect([...rows.options].map(option=>option.value)).toEqual([age, day]);
    expect(rows.value).toBe(age);
    await change(rows, day);
    await type("zzzzz");
    expect(rows.value).toBe(day);
    expect(host.textContent).toContain("No matching variables. Your current selection is retained.");
    expect(api.get).toHaveBeenCalledTimes(1);
    await type("couples");
    await change(rows, "c:prompt:communication-1");
    await act(async () => button(host, "Compare").click());
    expect(api.get.mock.lastCall[1].params.x).toBe("c:prompt:communication-1");
  });
});

test("single-question counts use their own denominator and clear a stale result after a change", async () => {
  const releases = [{ available: true, rows: [{ label: "Curious", count: 15 }, { label: "Interested", count: 45 }],
    sample_size: 60, round_to: 5 }, { available: false, rows: [], sample_size: null, minimum: 50, minimum_cell: 10 }];
  const api = { get: jest.fn(async (url, { params }) => ({
    data: url === "/admin/research/distribution" ? releases.shift() : payload(params),
  })) };
  await mounted(api, async host => {
    expect(host.textContent).toContain("Research coverage · 6 variables");
    expect(host.textContent).toContain("Skipped, unanswered, withheld and incompatible");
    await change(select(host, "Question or profile field"), first);
    await act(async () => button(host, "Show answer counts").click());
    expect(api.get.mock.lastCall[1].params).toMatchObject({ key: first });
    expect(host.textContent).toContain("Approximately 60 members");
    expect(host.textContent).toContain("25%");
    await change(select(host, "Question or profile field measure"), conditions[0]);
    expect(host.textContent).not.toContain("Approximately 60 members");
    await act(async () => button(host, "Show answer counts").click());
    expect(api.get.mock.lastCall[1].params).toEqual({ key: first, condition: conditions[0] });
    expect(host.textContent).toContain("Answer counts withheld");
    expect(host.textContent).not.toContain("Approximately 60 members");
  });
});

test("all variables and three comparison presets work while suppressed results offer no export", async () => {
  const api = { get: jest.fn(async (_, { params }) => ({ data: payload(params) })) };
  await mounted(api, async host => {
    expect(host.textContent).toContain("Comparison withheld");
    expect(host.textContent).toContain("at least 50");
    expect(host.textContent).toContain("at least 10");
    expect(button(host, "Download comparison (CSV)")).toBeUndefined();
    expect([...select(host, "Rows").options].map(option => option.value)).toEqual([age, "d:education", day, first, second, "c:prompt:communication-1"]);
    for (const [name, x, y] of [["Everyday and intimacy", day, first], ["Two intimacy questions", first, second], ["Age and intimacy", age, first]]) {
      await act(async () => button(host, name).click());
      expect(api.get).toHaveBeenLastCalledWith("/admin/research/trends", expect.objectContaining({ params: { x, y } }));
    }
    await change(select(host, "Columns"), age);
    expect(button(host, "Compare").disabled).toBe(true);
    await change(select(host, "Rows"), "d:education");
    await act(async () => button(host, "Compare").click());
    expect(api.get.mock.lastCall[1].params).toMatchObject({ x: "d:education", y: age });
  });
});

test("shows only released rounded aggregates and includes tag denominator in the results and export", async () => {
  const api = { get: jest.fn(async (_, { params }) => ({ data: payload(params, true) })) };
  await mounted(api, async host => {
    expect(host.querySelector("tbody").textContent).toContain("30 (75%)");
    expect(host.textContent).toContain("Exploratory Cramér’s V0.500");
    expect(button(host, "Download comparison (CSV)")).toBeDefined();
    await change(select(host, "Rows"), first);
    expect(button(host, "Compare").disabled).toBe(true);
    await change(select(host, "Rows measure"), conditions[0]);
    await change(select(host, "Columns measure"), conditions[1]);
    await act(async () => button(host, "Compare").click());
    expect(api.get.mock.lastCall[1].params).toMatchObject({ x: first, y: first, x_condition: conditions[0], y_condition: conditions[1] });
    expect(host.querySelector("caption").textContent).toContain("With more trust (among ‘It depends’ answers)");
  });
  const data = payload({ y_condition: "With more trust", x_choices: [' =2+2, "yes"', "18-24"] }, true);
  const csv = comparisonCsv(data);
  expect(csv.trim().split("\r\n")).toHaveLength(5);
  expect(csv).toContain('=2+2, ""yes""');
  expect(csv).toContain("\"' =2+2");
  expect(csv).toContain("With more trust (among ‘It depends’ answers)");
  expect(csv).toContain('"30","75","80","0.5"');
  expect(comparisonCsv(payload())).toBe("");
});

test("slow, failed or superseded comparisons cannot display earlier results and a failed comparison can retry", async () => {
  const requests = [];
  const api = { get: jest.fn((url, { params }) => {
    if (!params.x) return Promise.resolve({ data: payload({}, true) });
    return new Promise((resolve, reject) => requests.push({ resolve, reject, params }));
  }) };
  await mounted(api, async host => {
    await act(async () => button(host, "Everyday and intimacy").click());
    expect(host.querySelector("table")).toBeNull();
    await act(async () => button(host, "Two intimacy questions").click());
    await act(async () => requests[1].reject(new Error("failed")));
    expect(host.querySelector("[role=alert]").textContent).toContain("Retry");
    await act(async () => requests[0].resolve({ data: payload(requests[0].params, true) }));
    expect(host.querySelector("table")).toBeNull();
    await act(async () => button(host, "Retry").click());
    await act(async () => requests[2].resolve({ data: payload(requests[2].params, true) }));
    expect(host.querySelector("caption").textContent).toBe("Talk in advance? × Leave a note?");
  });
});

test("partners can compare the same starter, with the direction and unit retained in exports", async () => {
  const api = { get: jest.fn(async (_, { params }) => ({ data: payload(params, true) })) };
  await mounted(api, async host => {
    await act(async () => button(host, "Both partners’ answers").click());
    const key = "c:prompt:communication-1";
    expect(api.get.mock.lastCall[1].params).toEqual({ x: key, y: key, unit: "couple" });
    expect(host.querySelector("caption").textContent).toContain("Partners · either direction");
    expect(host.textContent).toContain("each direction gets half the weight");
    await change(select(host, "Compare answers from"), "couple_invitee");
    await act(async () => button(host, "Compare").click());
    expect(host.querySelector("caption").textContent).toContain("invitee answers rows");
    await change(select(host, "Compare answers from"), "member");
    expect(button(host, "Compare").disabled).toBe(true);
  });
  const csv = comparisonCsv(payload({ unit: "couple_inviter" }, true));
  expect(csv).toContain('"Approximate couples"');
  expect(csv).toContain('"Partners · inviter answers rows"');
});

test("search explicitly scans every page, ranks released measures together and opens the selected comparison", async () => {
  const hit = (y, cramers_v, sample_size) => ({ y, cramers_v, sample_size });
  const api = { get: jest.fn(async (route, { params }) => {
    if (!route.endsWith("discover")) return { data: payload(params, true) };
    if (params.cursor === 0) return { data: { catalog_version: "rev", checked: 2, total: 3, next_cursor: 2,
      matches: [hit(first, 0.3, 85)] } };
    expect(params.catalog_version).toBe("rev");
    return { data: { catalog_version: "rev", checked: 3, total: 3, next_cursor: null, matches: [hit(second, 0.7, 50)] } };
  }) };
  await mounted(api, async host => {
    expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => button(host, "Search all variables").click());
    expect(api.get).toHaveBeenCalledTimes(3);
    const items = [...host.querySelectorAll("ol li button")];
    expect(items[0].textContent).toContain("Leave a note?");
    expect(items[0].textContent).toContain("0.700");
    expect(items[1].textContent).toContain("Talk in advance?");
    await act(async () => items[0].click());
    expect(api.get.mock.lastCall[1].params).toMatchObject({ x: age, y: second });
    expect(host.querySelector("caption").textContent).toContain("Leave a note?");
  });
});

test("cancelling a discovery request or changing the row cannot expose partial rankings", async () => {
  let pending;
  const api = { get: jest.fn((route, { signal, params }) => route.endsWith("discover")
    ? new Promise(resolve => { pending = { signal, resolve }; })
    : Promise.resolve({ data: payload(params) })) };
  await mounted(api, async host => {
    await act(async () => button(host, "Search all variables").click());
    expect(button(host, "Cancel search")).toBeDefined();
    await act(async () => button(host, "Cancel search").click());
    expect(pending.signal.aborted).toBe(true);
    await act(async () => pending.resolve({ data: { checked: 1, total: 1, next_cursor: null, matches: [] } }));
    expect(host.textContent).not.toContain("No comparisons met");
    await act(async () => button(host, "Search all variables").click());
    await change(select(host, "Rows"), first);
    expect(pending.signal.aborted).toBe(true);
    expect(button(host, "Search all variables")).toBeDefined();
  });
});

test("one catalog action checks every pair and displays only the associations confirmed in the final recheck", async () => {
  const found = { x: day, y: first, x_condition: null, y_condition: null, indices: [7, 28], cramers_v: 0.7, sample_size: 80 };
  const withdrawn = { ...found, x: age, indices: [0, 28], cramers_v: 0.9 };
  const api = {
    get: jest.fn(async (_, { params }) => ({ data: payload(params, true) })),
    post: jest.fn(async (_, body) => {
      if (body.cursor === 0) return { data: { phase: "screening", plan: "abc", completed: 1, total: 1, next_cursor: 1 } };
      if (body.cursor === 1) return { data: { phase: "comparing", plan: "abc", completed: 1, total: 1,
        tested: 20, next_cursor: 2, matches: [found, withdrawn], verify: true } };
      expect(body.review).toEqual([withdrawn.indices, found.indices]);
      return { data: { phase: "done", matches: [found] } };
    }),
  };
  await mounted(api, async host => {
    expect(api.post).not.toHaveBeenCalled();
    await act(async () => button(host, "Search whole catalog").click());
    const results = host.querySelector("[aria-label='Whole catalog search']");
    expect(api.post).toHaveBeenCalledTimes(3);
    expect(api.post.mock.calls[0][1].x).toBeUndefined();
    expect(results.textContent).toContain("20 candidate comparisons");
    expect(results.querySelectorAll("ol li")).toHaveLength(1);
    expect(results.textContent).toContain("0.700");
    expect(results.textContent).not.toContain("0.900");
    await act(async () => results.querySelector("ol button").click());
    expect(api.get.mock.lastCall[1].params).toMatchObject({x: day, y: first, unit: "member"});
  });
});

test("cancelling a catalog scan discards provisional results", async () => {
  let end;
  const api = { get: jest.fn(async (_, {params}) => ({data: payload(params)})),
    post: jest.fn((_, body, {signal}) => new Promise(resolve => { end={signal,resolve}; })) };
  await mounted(api, async host => {
    await act(async () => button(host, "Search whole catalog").click());
    await act(async () => button(host, "Cancel catalog search").click());
    expect(end.signal.aborted).toBe(true);
    await act(async () => end.resolve({data:{phase:"done",matches:[]}}));
    expect(host.textContent).not.toContain("No variable pairs met");
  });
});

test("released exact correlations carry their own sample and sign into the report and CSV", async () => {
  const data = { ...payload({}, true), y: "height_band", y_choices: ["Under 150 cm", "150–159 cm"],
    variables: [...variables, { id: "height_band", domain: "demographics", label: "Height range" }],
    pearson: { r: -0.75, sample_size: 65 } };
  await mounted({ get: jest.fn(async () => ({ data })) }, async host => {
    expect(host.textContent).toContain("Pearson correlation for exact measurements");
    expect(host.textContent).toContain("-0.750");
    expect(host.textContent).toContain("approximately 65 members");
  });
  expect(comparisonCsv(data)).toContain('"Pearson r (exact measurements)","Approximate numeric sample"');
  expect(comparisonCsv(data)).toContain('"-0.75","65"');
  expect(comparisonCsv({ ...data, pearson: null })).not.toContain("Pearson");
});

test("registered comparisons use the currently displayed variables and discard stale followup results on refresh", async () => {
  const report = {...payload({}, true), instrument: "a".repeat(64), observed_at: "2026-11-09T12:00:00Z"};
  expect(comparisonCsv(report)).toContain(report.instrument);
  const row = {id:"study", selection:{unit:"member",x:report.x,y:report.y}, labels:{x:"Age range", y:"A study topic"}, instrument:"a".repeat(64), registered_at:"2026-11-01T00:00:00Z"};
  const api = {
    get: jest.fn(async url => {
      if(url === "/admin/research/registrations") return {data:{items:[row],coverage:{available:false}}};
      if(url.endsWith("/study")) return {data:{state:"available",sample_size:55,cramers_v:0.32,
        baseline:{sample_size:55,cramers_v:0.32},patterns:[{row:"18-24",column:"Curious",baseline:{count:25,total:25},future:{count:0,total:25}},
          {row:"25-34",column:"Curious",baseline:null,future:{count:20,total:30}}]}};
      return {data:report};
    }),
    post: jest.fn(async()=>({data:row})),
  };
  await mounted(api, async host => {
    const click = async label => act(async()=>button(host,label).click());
    await click("Prospective comparisons");
    await click("Open registered comparisons");
    expect(host.textContent).toContain("Participation counts are withheld");
    await click("Register the displayed comparison");
    expect(api.post).toHaveBeenCalledWith("/admin/research/registrations",expect.objectContaining({unit:"member",x:report.x,y:report.y,expected_instrument:report.instrument}));
    await click("Check future cohort");
    expect(host.textContent).toContain("Future cohort Cramér’s V 0.32");
    const table = [...host.querySelectorAll("table")].find(t => t.caption?.textContent.includes("baseline and future"));
    expect([...table.tBodies[0].rows[0].cells].map(cell=>cell.textContent)).toEqual(["18-24","Curious","25 of 25 (100%)","0 of 25 (0%)"]);
    expect(table.tBodies[0].rows[1].cells[2].textContent).toBe("No respondents in this row");
    await click("Refresh registrations");
    expect(host.textContent).not.toContain("Future cohort Cramér’s V 0.32");
  });
});
