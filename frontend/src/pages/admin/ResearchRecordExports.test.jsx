import { act } from "react";
import { createRoot } from "react-dom/client";
import ResearchRecordExports from "./ResearchRecordExports";

const sample = "m_" + "a".repeat(32);
const other = "m_" + "b".repeat(32);
let host, root, anchor;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  URL.createObjectURL = jest.fn(()=>"blob:test");
  URL.revokeObjectURL = jest.fn();
  anchor = jest.spyOn(HTMLAnchorElement.prototype,"click").mockImplementation(()=>{});
});
afterEach(() => {
  act(()=>root.unmount()); host.remove(); anchor.mockRestore();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});
const button = label => [...host.querySelectorAll("button")].find(el=>el.textContent===label);
const click = el => act(async()=>el.click());
const select = (el,value) => act(async()=>{el.value=value;el.dispatchEvent(new Event("change",{bubbles:true}));});

test("one coded record, paging, withdrawn code and a header-only combined file",async()=>{
  let withdrawn=false;
  const api={get:jest.fn(async(path, options)=>{
    if (path.endsWith("/units")) return {data:options.params.after ? {items:[other], next:null} : {items:[sample], next:sample}};
    if (withdrawn && options.params.unit_code) throw Object.assign(new Error(),{response:{status:404}});
    return {data:new Blob(["headings"]),headers:{"x-research-empty":options.params.unit_code ? "0" : "1"}};
  })};
  await act(async()=>root.render(<ResearchRecordExports api={api} />));
  expect(api.get).not.toHaveBeenCalled();
  expect(button("Download Individual CSV").disabled).toBe(true);
  await click(button("Browse eligible codes"));
  await click(button("Load more codes"));
  expect(api.get).toHaveBeenLastCalledWith("/admin/research/exports/member/units",expect.objectContaining({params:{limit:50,after:sample}}));
  await select(host.querySelector('[aria-label="Eligible research codes"]'),other);
  await click(button("Download Individual CSV"));
  expect(api.get).toHaveBeenLastCalledWith("/admin/research/exports/member.csv",expect.objectContaining({params:{unit_code:other},responseType:"blob"}));
  expect(anchor.mock.instances[0].download).toContain(other);
  withdrawn=true;
  await click(button("Download Individual CSV"));
  expect(host.textContent).toContain("This code is unavailable");
  expect(anchor).toHaveBeenCalledTimes(1);
  await click(button("Couples"));
  expect(host.querySelector('input').value).toBe("");
  await select(host.querySelector("select"),"all");
  await click(button("Download Couples CSV"));
  expect(api.get).toHaveBeenLastCalledWith("/admin/research/exports/couple.csv",expect.objectContaining({params:{}}));
  expect(host.textContent).toContain("column headings only");
  expect(anchor).toHaveBeenCalledTimes(2);
});

test("a response from the previous scope cannot trigger a download", async()=>{
  let resolve;
  const api={get:jest.fn(()=>new Promise(done=>{resolve=done;}))};
  await act(async()=>root.render(<ResearchRecordExports api={api} />));
  await select(host.querySelector("select"),"all");
  await click(button("Download Individual CSV"));
  const signal=api.get.mock.calls[0][1].signal;
  await click(button("Couples"));
  expect(signal.aborted).toBe(true);
  await act(async()=>resolve({data:new Blob(["old"])}));
  expect(anchor).not.toHaveBeenCalled();
});
