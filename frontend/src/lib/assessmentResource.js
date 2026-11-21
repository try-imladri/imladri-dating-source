import { waitForProfileWrites } from "./profileWriteBarrier";
import { consumeEntryRead } from "./entryRead";

// Fresh owner-bound data, one authentication/settings pass, no member-data cache.
export async function readAssessmentProfile(api, owner, options = {}) {
  const { includeIntimacy = false, ...requestOptions } = options;
  const endpoint = includeIntimacy ? "/me/assessments?include_intimacy=true" : "/me/assessments";
  await waitForProfileWrites(api, owner);
  if (options.signal?.aborted) throw Object.assign(new Error("Request cancelled"), { code: "ERR_CANCELED" });
  try {
    const { data } = await (consumeEntryRead(api, owner, endpoint, options.signal) || api.get(endpoint, {
      ...requestOptions, headers: { ...requestOptions.headers, "X-Questionnaire-Owner": owner },
    }));
    if (data.owner_id !== owner) throw new Error("Your signed-in account changed. Reopen Personal.");
    return data;
  } catch (error) {
    // Only an older API needs the compatibility reads, never a failed request.
    if (error.response?.status !== 404) throw error;
    const [quiz, profile] = await Promise.all([api.get("/quiz", requestOptions), api.get("/me/about", requestOptions)]);
    return { quiz: quiz.data, ...profile.data };
  }
}
