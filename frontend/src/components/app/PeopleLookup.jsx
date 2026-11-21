import { useState } from "react";
import { Link } from "react-router-dom";
import { useMemberPages, ListContinuation } from "./MemberPages";

export default function PeopleLookup({api}) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const page = useMemberPages(api, query ? "/people/lookup?q=" + encodeURIComponent(query) : null, {size:20});
  return <div className="mb-4 text-sm">
    <form className="flex flex-wrap items-center gap-2" onSubmit={event => { event.preventDefault(); setQuery(input.trim()); }}>
      <input type="search" aria-label="Find a person by nickname" placeholder="Find a person by nickname" maxLength={80}
        value={input} onChange={e=>setInput(e.target.value)} className="min-w-0 bg-transparent rounded border border-[var(--line)] px-3 py-2" />
      <button className="underline" type="submit">Find</button>
      {query && <button className="underline" type="button" onClick={()=>{setInput("");setQuery("");}}>Clear</button>}
    </form>
    {query && <div className="mt-2" aria-live="polite">
      {page.error ? <button className="underline" onClick={page.reload}>Couldn’t load matches. Try again</button> :
        !page.rows ? "Searching…" : <><p>{page.total} {page.total===1 ? "match" : "matches"} including archived people</p>
        <ul>{page.rows.map(row=><li key={row.id}><Link className="underline" to={"/app/person/"+encodeURIComponent(row.id)}>{row.nickname}</Link>{row.archived && " · Archived"}</li>)}</ul>
        <ListContinuation page={page} label="matches" /></>}
    </div>}
  </div>;
}