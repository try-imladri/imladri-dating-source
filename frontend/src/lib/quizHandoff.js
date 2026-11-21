const storageKey = "imladri:public-quiz:handoff";
const lifetime = 24 * 60 * 60 * 1000;

export function clearQuizHandoff() {
  try { sessionStorage.removeItem(storageKey); } catch {}
}

export function keepQuizHandoff(type) {
  if (!type || typeof type.slug !== "string" || !/^[a-z0-9-]{1,100}$/.test(type.slug) || typeof type.name !== "string" || !type.name.trim()) return false;
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ slug:type.slug, name:type.name.slice(0,150), until:Date.now()+lifetime }));
    return true;
  } catch { return false; }
}

export function readQuizHandoff() {
  try {
    const item = JSON.parse(sessionStorage.getItem(storageKey));
    if (item && typeof item.name === "string" && item.name.trim() && typeof item.slug === "string" &&
        /^[a-z0-9-]{1,100}$/.test(item.slug) && Number.isFinite(item.until) &&
        item.until > Date.now() && item.until <= Date.now()+lifetime) return item;
  } catch {}
  clearQuizHandoff();
  return null;
}

export function alternativeResult(quiz, answers, visibleTypes) {
  const current = quiz.outcomes[answers[0]][answers[1]][answers[2]];
  for (const change of [2,1]) {
    for (let choice=0; choice<quiz.questions[change].a.length; choice++) {
      const alternative = answers.map((value, i)=>i===change ? choice : value);
      const slug = quiz.outcomes[alternative[0]][alternative[1]][alternative[2]];
      const type = visibleTypes.find(item=>item.slug===slug && item.unlocked!==false);
      if (slug!==current && type) return {type, question:quiz.questions[change].q, choice:quiz.questions[change].a[choice]};
    }
  }
  return null;
}