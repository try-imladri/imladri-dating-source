// The edge and the reader use the same public fields for a shared article.
export const publicSite = "https://imladri.com";
export const publicBrandImage = "/img/imladri-dating-social-v1.png";
export const publicBrandImageAlt = "Imladri — meet your type. Understand your fit. Intentional dating, your attraction, your preferences, your choice.";

export function catalogArticleMetadata(section, slug, item) {
  const isArchetype = section === "types";
  const edition = item.gender === "female" ? "Female" : "Male";
  const character = isArchetype && item.sections?.characters?.find(person => person.image_url);
  const photo = isArchetype ? character?.image_url : item.photo_url;
  const hasPhoto = /^\/(?:api\/media\/|img\/)[a-zA-Z0-9/._-]+$/.test(photo || "") && !photo.split("/").includes("..");
  return {
    type: "article",
    canonical: publicSite + "/" + section + "/" + slug,
    title: isArchetype ? item.name + " · " + edition + " Archetype · Imladri" : item.name + " · Imladri Public Figures",
    description: item.identity || item.one_liner || item.name,
    image: publicSite + (hasPhoto ? photo : publicBrandImage),
    alt: hasPhoto ? (isArchetype ? character.name : item.name) : publicBrandImageAlt,
  };
}
