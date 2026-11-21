const latinLetters = { ø: "o", ł: "l", đ: "d", ð: "d", þ: "th", æ: "ae", œ: "oe", ß: "ss", ı: "i" };
function words(text) {
  return (text || "").normalize("NFKD").toLowerCase().replace(/\p{M}/gu, "")
    .replace(/[øłđðþæœßı]/g, letter => latinLetters[letter])
    .match(/[\p{L}\p{N}]+/gu) || [];
}

export function filterFigures(figures, query) {
  const tokens = words(query);
  if (!tokens.length) return query.trim() ? [] : figures;
  const joined = tokens.join("");
  return figures.filter(figure => [figure.name, figure.slug, ...(figure.name_variants || [])].some(value => {
    const name = words(value);
    return name.join("").includes(joined) ||
      (joined.length >= 3 && name.map(part => part[0]).join("") === joined) ||
      (tokens.length > 1 && tokens.every(token => name.some(part => part.startsWith(token))));
  }));
}
