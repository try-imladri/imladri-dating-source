import { useNavigate, useOutletContext } from "react-router-dom";
import { IntimacyPanel } from "@/components/app/Intimacy";
import { AssessmentHeader, AssessmentLoadState, useAssessmentProfile } from "./AssessmentProfile";

export default function IntimacyPage({ withinCouples = false }) {
  const context = useOutletContext();
  return <MemberIntimacy key={context.me.user_id} {...context} withinCouples={withinCouples} />;
}

function MemberIntimacy({ me, api, withinCouples }) {
  const nav = useNavigate();
  const resource = useAssessmentProfile(api, me.user_id, { includeIntimacy: true });
  if (!resource.data) return <AssessmentLoadState resource={resource} label="your Sex & Intimacy questionnaires" />;
  const { quiz, about } = resource.data;
  const underage = about.demographics?.age != null && about.demographics.age < 18;
  return <div className="space-y-4" data-testid="intimacy-page">
    <AssessmentHeader title="Sex & Intimacy" description="Take a moment to think about your own interests and boundaries."
      me={me} withinCouples={withinCouples} />
    <IntimacyPanel underage={underage} profileAdultEligible={typeof about.demographics?.age === "number" && about.demographics.age >= 18}
      onExit={() => nav((withinCouples ? "/app/couples/personal" : "/app/personal") + "?tab=basics")}
      savedProfile={resource.portrait} accountSaving owner={me.user_id} overrides={quiz.intimacy_copy} api={api}
      takeInitialAnswers={resource.takeInitialIntimacyAnswers}
      catalogVersion={quiz.research_catalog_version} intimacySignature={quiz.research_intimacy_signature} />
  </div>;
}
