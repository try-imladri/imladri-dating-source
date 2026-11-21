const key = "imladri.practice.pending-turn";

function turnsFor(member) {
  const saved = JSON.parse(sessionStorage.getItem(key));
  const records = Array.isArray(saved) ? saved : saved ? [saved] : [];
  return records.filter(record => record.owner === member.user_id && record.created === member.created_at &&
    record.revision === (member.content_revision || 0) && Date.now() < record.expires);
}

function save(records) {
  if (records.length) sessionStorage.setItem(key, JSON.stringify(records));
  else sessionStorage.removeItem(key);
}

export function clearPracticeTurn(member, session, turnKey) {
  try {
    if (!member) sessionStorage.removeItem(key);
    else {
      const saved = JSON.parse(sessionStorage.getItem(key));
      const records = Array.isArray(saved) ? saved : saved ? [saved] : [];
      save(records.filter(record => record.owner !== member.user_id || record.created !== member.created_at ||
        record.revision !== (member.content_revision || 0) || record.session !== session || record.turn?.key !== turnKey));
    }
  } catch { /* Storage may be disabled. */ }
}

export function readPracticeTurn(member, session) {
  try {
    const records = turnsFor(member);
    save(records);
    return records.find(record => record.session === session)?.turn || null;
  } catch { return null; }
}

export function storePracticeTurn(member, session, turn) {
  try {
    const otherSessions = turnsFor(member).filter(record => record.session !== session);
    save([...otherSessions, {owner: member.user_id, created: member.created_at,
      revision: member.content_revision || 0, session, turn, expires: Date.now() + 60 * 60 * 1000}]);
  } catch { /* In-memory recovery still works. */ }
}
