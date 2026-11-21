import { Link } from "react-router-dom";
import { useConfig, signupPath } from "@/components/public/PublicLayout";
import { setOrigin } from "@/lib/api";
import AssessmentMethods from "@/components/public/AssessmentMethods";

export function PublicPage({ eyebrow, title, children, testid }) {
  return (
    <article className="px-5 sm:px-8 lg:px-12 pt-12 grid grid-cols-1 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)] gap-8 lg:gap-16" data-testid={testid}>
      <div><div className="lg:sticky lg:top-24"><p className="eyebrow">{eyebrow}</p>
      <h1 className="font-display font-black uppercase text-[clamp(2.5rem,13vw,3.75rem)] sm:text-8xl mt-3 [overflow-wrap:anywhere]">{title}</h1></div></div>
      <div className="max-w-4xl space-y-5 leading-relaxed text-[var(--ink)]/90 [&_h2]:font-display [&_h2]:font-black [&_h2]:uppercase [&_h2]:text-3xl [&_h2]:mt-10 [&_h2]:text-[var(--ink)]">{children}</div>
    </article>
  );
}

const SIX = [
  ["What they're actually doing", "Only what's observable: what they said, what they did, and when. No mind-reading."],
  ["What it likely means", "The most likely read — with an honest confidence level. Low means low."],
  ["What changed since last time", "If new evidence shifts an earlier read, we say so out loud: \"previously I read X as Y; this message shifts it because…\""],
  ["Your best move", "One move. Not ten options."],
  ["What to say", "Get words you can use, or a clear recommendation to leave it there when someone has asked for no contact."],
  ["What not to do", "The trap this situation sets for you."],
];

export function LegacyHowItWorks() {
  return (
    <PublicPage eyebrow="Plain words, no magic" title="How it works" testid="how-it-works-page">
      <p>You can work through a conversation privately in Individual, or agree on something to try together in Couples. It's built on one rule: <strong>be confident about what people do, honest about why they do it.</strong></p>
      <nav aria-label="Choose a walkthrough" className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
        <a href="#individual-example" className="underline">See an Individual conversation</a>
        <a href="#couples-example" className="underline">See what partners share in Couples</a>
      </nav>
      <h2>Explore what matters to you</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <section id="your-type" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Your Type</h3>
          <p className="text-sm mt-2">Compare original synthetic adult portraits and explore the visual preferences in your choices. Your results build a private visual-preference profile. Portrait choices do not establish someone’s personality or predict relationship compatibility.</p>
          <Link className="inline-block underline text-sm mt-3" to="/app/your-type">Explore Your Type</Link>
        </section>
        <section id="individual-profile" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Individual</h3>
          <p className="text-sm mt-2">Answer questionnaires about your personality and relationship patterns. Add detail over time, reflect on your results and use that context when you work through a conversation. These are tools for reflection, not a diagnosis.</p>
          <Link className="inline-block underline text-sm mt-3" to="/app/personal">Explore your profile</Link>
        </section>
        <section id="sexual-compatibility" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Sex &amp; Intimacy</h3>
          <p className="text-sm mt-2">Optional questionnaires for adults 18 and older help you describe preferences, comfort, boundaries and conditions. Your Individual answers stay separate from Couples. You choose what to share with a partner; this does not produce a guaranteed compatibility score.</p>
          <Link className="inline-block underline text-sm mt-3" to="/app/intimacy">Explore Sex &amp; Intimacy</Link>
        </section>
        <section id="communication" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Communication</h3>
          <p className="text-sm mt-2">Work through what was actually said, consider possible meanings and practise a reply in the tone you want. You can rehearse privately or use the Couples tools for a conversation you both choose to work on.</p>
          <Link className="inline-block underline text-sm mt-3" to="/app/practice">Try conversation practice</Link>
        </section>
      </div>
      <h2>1. Choose who’s on your mind</h2>
      <p>Use a nickname for a partner, friend, relative or coworker. Add how you know them and what you want help with. You can add how they text or handle conflict later. A <em>profile detail</em> meter shows how complete it is. It measures completeness, not accuracy.</p>
      <h2>2. You share a conversation</h2>
      <p>Paste the words you want to share, or blur private details on a screenshot. DeepSeek can read the screenshot you preview. Check who said what before you ask for advice.</p>
      <h2>3. You get a six-part answer</h2>
      <div className="grid gap-3">
        {SIX.map(([t, d], i) => (
          <div key={t} className="card p-4"><p className="font-mono text-xs text-[var(--lime)]">PART {i + 1}</p><p className="font-semibold mt-1">{t}</p><p className="text-sm text-[var(--muted)] mt-1">{d}</p></div>
        ))}
      </div>
      <h2>4. Review what the app remembers</h2>
      <p>After each answer the app proposes what to remember: things that happened, interpretations, the current working read. You approve, edit or reject each one. Profile details you type in yourself are saved directly. You can review, edit or delete saved memories. If you saved a game-plan outcome and also made it a memory, deleting the memory keeps the outcome in the original plan.</p>
      <p>Something they actually did or said counts for more than a theory. If you add an interpretation, the app identifies it as yours.</p>
      <h2>5. The report updates when the evidence does</h2>
      <p>Each person gets a report: likely traits, communication and attachment style, triggers, conflict style, what tends to work and where communication stalls. Every point shows its confidence and where it came from. Sections without enough evidence say so instead of guessing. It refreshes only when something meaningful arrives — a new conversation, an outcome you logged, a deletion — and tells you what changed and why.</p>
      <h2>6. Tell it what actually happened</h2>
      <p>Every piece of advice has a "What actually happened" button. Outcomes are how the read gets sharper — and how you can check whether the advice is any good.</p>
      <h2 id="examples" className="scroll-mt-28">Individual and Couples examples</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <section id="individual-example" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Individual · a short reply</h3><ol className="text-sm mt-2 list-decimal pl-5 space-y-2">
          <li>You suggest dinner and they reply “maybe.” You paste both messages. One word leaves their meaning open.</li>
          <li>You choose a warm tone and ask, “Would another day work better?” They reply, “Saturday works. I have a late shift Friday.”</li>
          <li>You record the outcome and choose whether to save the shift as context. Your next read can draw on what they actually told you.</li>
        </ol><Link className="inline-block underline text-sm mt-3" to="/app/start">Open Individual</Link></section>
        <section id="couples-example" className="card p-4 scroll-mt-28"><h3 className="font-semibold">Couples · Friday plans keep changing</h3><ol className="text-sm mt-2 list-decimal pl-5 space-y-2">
          <li>Alex privately writes “I feel left out” and “A heads-up would help.” Alex shares only the second line.</li>
          <li>Jordan shares “I sometimes learn my shift late.” When both have shared, each sees the two selected lines. Alex’s first line stays private.</li>
          <li>They both accept “Text before changing Friday plans” and pick next Friday for a check-in.</li>
          <li>Alex chooses “Helped” and Jordan chooses “Mixed.” Alex chooses to share first. Their results and any notes become visible to each other only after Jordan also shares.</li>
        </ol><Link className="inline-block underline text-sm mt-3" to="/app/couples">Open Couples</Link></section>
      </div>
      <p className="text-sm"><Link className="underline" to="/legacy/plans">Compare private tool plans and message allowances</Link> · <Link className="underline" to="/privacy">Read how your information is used</Link></p>
      <AssessmentMethods />
      <p className="text-sm text-[var(--muted)]">Imladri is an interpretation tool, not therapy, and never a diagnosis. If you are ever unsafe, contact local emergency services.</p>
      <Link to="/legacy" className="btn-lime inline-block px-6 py-3 mt-4" data-testid="how-cta">Open private tools</Link>
    </PublicPage>
  );
}

export function HowItWorks() {
  const config = useConfig();
  return (
    <PublicPage eyebrow="Intentional dating · Founding beta" title="How it works" testid="how-it-works-page">
      <p className="text-lg">Meet your type. Understand your fit. Imladri brings attraction, relationship intentions, and preferences into a conversation you both choose to have.</p>
      <h2>1. Join the founding community</h2>
      <p>Dating is for adults 18 and older. Sign in and choose whether to enroll. We’re building a local community; who is available depends on participation, profile review, and reciprocal preferences.</p>
      <h2>2. Build a profile you mean</h2>
      <p>Describe yourself, what you’re looking for, and the things you want someone to know. Review what will appear on your dating profile. Your existing private Imladri records are not automatically turned into a public profile.</p>
      <h2>3. Add context at your pace</h2>
      <p>Personality and relationship questions can help you describe your communication style, values, and preferences. Choose what to share. Your Type is a separate private exercise with original synthetic adult portraits; those portraits are not members and your choices do not establish someone else’s personality.</p>
      <section className="card p-5" aria-label="Intimacy choices"><h2 className="!mt-0">Intimacy is optional</h2><p className="mt-3">You can skip intimacy questions and still use dating. Permission to use a preference for matching is separate from permission to share it with a match. Raw private answers are not a public dating profile.</p></section>
      <h2>4. Express mutual interest</h2>
      <p>When introductions are available, explore people whose preferences align with yours, like someone you want to meet, and start a conversation after mutual interest. A preference summary is a discussion starter, not a proven compatibility score.</p>
      <h2>5. Stay in control</h2>
      <p>You can edit your profile, pause discovery, unmatch, block, or report a concern. Choose what you share and when. Imladri does not guarantee someone’s identity, chemistry, a match, or a successful relationship.</p>
      <p><Link className="underline" to="/plans">Free founding beta details</Link> · <Link className="underline" to="/privacy">Your data and choices</Link> · <Link className="underline" to="/legacy">Existing private tools</Link></p>
      <Link to={signupPath(config, "how-it-works")} onClick={() => setOrigin("how-it-works")} className="btn-lime inline-flex px-6 py-3" data-testid="how-cta">Join the founding community</Link>
    </PublicPage>
  );
}

export function About() {
  return (
    <PublicPage eyebrow="About" title="About Imladri" testid="about-page">
      <p className="text-lg">Imladri is building an intentional dating community around a simple idea: attraction matters, and so does understanding what each person wants.</p>
      <p>Your personality, relationship preferences, and boundaries can give a conversation more context. You choose which parts to share. Questions help people describe themselves; they do not promise chemistry or a perfect match.</p>
      <p>Our founding beta starts by bringing the community together. Enrollment does not guarantee an immediate introduction. We want to learn whether the people who join can find reciprocal interest and useful conversations.</p>
      <p>Existing Individual, Couples, Your Type, and practice tools are still available in <Link className="underline" to="/legacy">Private tools</Link>. Dating enrollment is separate from your private records.</p>
      <p><Link className="underline" to="/how-it-works">How it works</Link> · <Link className="underline" to="/support">Contact support</Link></p>
    </PublicPage>
  );
}

export function Privacy() {
  return (
    <PublicPage eyebrow="Your data" title="Privacy" testid="privacy-page">
      <section aria-label="Dating enrollment" className="card p-5"><h2 className="!mt-0">Dating is a separate choice</h2><p className="mt-3">Enrolling in dating creates a separate dating profile. Existing Individual notes, questionnaire answers, Your Type choices, and Couples records are not automatically published or imported into it. You review the profile information you choose to share.</p><p className="mt-3">Optional intimacy preferences have separate permissions for matching use and sharing with a match. Enabling matching use does not publish your private answers. Research choices remain separate from dating enrollment. See the dating consent screen for the current notice and controls.</p></section>
      <section aria-label="How your information is used" className="card p-4">
        <h2 className="!mt-0">Who can see what?</h2>
        <dl className="text-sm mt-3 space-y-3">
          <div><dt className="font-semibold">Your workspace</dt><dd>Your Individual notes, questionnaire answers and unshared Couples drafts stay in your account. Your partner cannot open them.</dd></div>
          <div><dt className="font-semibold">Your partner</dt><dd>Both of you choose the lines to share in a conversation. You can both see each other’s check-in result and any note once both of you choose to share that check-in.</dd></div>
          <div><dt className="font-semibold">DeepSeek</dt><dd>When you request AI coaching or screenshot transcription, the app sends the relevant conversation, selected context or previewed image to the AI provider.</dd></div>
          <div><dt className="font-semibold">Optional research</dt><dd>Settings offers separate choices for group research and staff downloads of coded Individual or Couples records. You can make an agreement and check in with research turned off. A scheduled return counts toward research only when both partners opt in before making the agreement. Either partner can withdraw in Partner &amp; settings.</dd></div>
        </dl>
      </section>
      <p>This notice describes how imladri.com handles the information you share. You can browse public pages or send a support request without creating an account.</p>
      <h2>Account and activity</h2>
      <p>Your answers save to your account as you go, including unfinished questionnaires. One main consent screen after sign-in explains how Imladri stores your information and uses relevant details to personalize advice. It also covers optional questionnaire, Sex &amp; Intimacy, Your Type, conversation tone and everyday-choice research, staff answer-history review, and coded Individual and current-Couples downloads. You can agree to all available purposes or customize your choices on that same screen. Optional research is not required to use the rest of the app. We record the notice version and hash, date, action and the permissions you chose. You can always change or withdraw those permissions in Settings → Data &amp; privacy.</p>
      <p>Newly saved questionnaire changes have a dated private history, including the question wording, answer revisions and recorded skips. Your account export includes this history. Removing an answer or clearing the related content removes its retained answer history. Clearing a demographic field also removes earlier demographic snapshot values. Your research participation choices continue to control which records contribute to research.</p>
      <p>Staff answer-history access is an optional purpose on the main consent screen and in Settings; it is not enabled without your approval. If you enable it with a confirmed adult profile age, authorized staff may review and download later answer history, including written answers, demographic snapshots, Sex &amp; Intimacy answers, Your Type choices and private Couples contributions where your other applicable research permissions also allow access. Earlier answers are not added automatically. A Couples history view requires both partners’ permission. This permission does not make private answers visible to a partner. Turning it off stops future staff access under this permission; earlier downloads may still exist. Staff timeline access and downloads are logged without copying answer text into the access log.</p>
      <p>Clerk handles sign-in and account credentials. Imladri uses your account ID and email to connect you to your profiles, settings and plan. The app records activity such as completed reads and reply usage to operate its features and limits.</p>
      <h2>Your Type</h2>
      <p>Your Type saves your choices among original synthetic adult portraits, the audience you selected, issued portraits, declared views, pre-choice predictions, revisions and undo history. These records build a private visual-preference profile within that portrait bank. Your partner cannot open it. The feature uses statistical calculations without AI-provider calls or message credits; it does not infer identity, ethnicity, sexual orientation, personality or relationship compatibility from portraits.</p>
      <p>Your profile can also save the sexuality and viewer gender you report. When you start a Your Type attempt, it records the reported viewer gender, sexuality and rated audience that apply to that attempt. Later profile edits do not rewrite this context or fill in older attempts. These private settings select which portraits you rate; they are not an inference about you.</p>
      <p>Your Type research is a separate, optional permission that is off by default. Only attempts started after the current permission can contribute, and profile or questionnaire comparisons also require current questionnaire research permission. Coded action downloads additionally require the Individual record-export permission. Group comparisons hide small samples and keep image and model editions separate. Turning permission off excludes you from later research reports and downloads; an earlier downloaded copy may still exist. You can export your owned history in Settings, remove an attempt and its retained history, or clear your saved content. After deletion, a non-identifying marker prevents an old request from recreating the removed attempt.</p>
      <p>The dashboard counts members who open Couples or finish a sample each day. For each conversation card, it counts pairs who start directly or through the sample, both share an answer, agree on a next step, or complete a check-in together. These counts exclude what either partner wrote. You can also opt in under Settings to contribute three everyday quiz choices and selected follow-up results to group statistics. Each represented choice must have at least 10 participating members before a breakdown appears. Turning this preference off removes your contribution when the reports are next loaded. Private conversation text, notes and Sex &amp; Intimacy answers are excluded from these breakdowns.</p>
      <h2>Couples</h2>
      <p>When a confirmed couple is eligible to compare assessments, the main consent screen identifies the current-pair sharing choices for personality and Sex &amp; Intimacy. Each partner makes their own choice. Comparisons and related coaching use only categories both partners share, and that permission does not apply to a different partner. Private notes, written answers, conversations, demographics and Your Type are excluded from assessment sharing. You can change these choices in Settings → Data &amp; privacy. A shared preference is not consent to an activity, a diagnosis or a prediction of relationship success.</p>
      <p>Each partner has a separate account. Your Couples drafts stay private. You preview the words you want to share, and your partner can see them when you both contribute to the same conversation. Both partners must share a check-in before either can see the other’s result or note. Both partners approve the wording of an agreement or a saved learning. These activities use authored questions and guidance.</p>
      <p>You can withdraw shared words or leave in Partner &amp; settings. Withdrawing words also removes the shared work that depends on them. Someone may already have read or copied them. Your Individual profile and intimacy answers stay separate. Couples reminders are optional and use neutral wording.</p>
      <p>Admin sees aggregate counts of invitations, agreements, check-ins and mutually approved learnings. It also counts how many active pairs have both shared a starter or a reflection. Withdrawing shared words or removing a learning updates those current counts. Reports about how a pair said it went use both partners’ separate Couples statistics preferences and the fixed result they each chose to share. Each represented result needs at least 10 pairs. Private drafts, shared wording, check-in notes and learning sentences stay out of these reports.</p>
      <h2>Conversations and AI</h2>
      <p>When you ask for Couples tone coaching, DeepSeek receives the wording needed for that request: your saved draft, or the words both partners released in that conversation. Relevant, bounded assessment comparisons may also inform coaching when both partners have shared the applicable categories. Your coaching and suggested replies are private. You choose which words to share with your partner.</p>
      <p>Individual tone estimates use the messages you submit and, when you continue a thread, its earlier messages. You can correct a reading and choose a reply style. Activity reports count feature use and feedback. Optional tone research compares structured categories and self-reported outcomes in groups. Participation is separate from questionnaire research, and pair comparisons require both partners to opt in.</p>
      <p>Imladri sends your question and relevant context to DeepSeek to generate replies, reports and game plans. That context can include conversation text, profile details, quiz answers, memories, previous advice and outcomes. Use nicknames and remove details you want to keep private.</p>
      <p>In your Individual workspace, Imladri saves the everyday answers you enter. Adults can save and resume Sex &amp; Intimacy answers, conditions and deliberate skips in their account. The questionnaire tells you when an answer has been saved. Each partner in Couples can save their own age, gender, sex, ethnicity, education and height. A partner’s private profile remains under their control.</p>
      <p>Adults may separately rate whether an intimacy summary expressed their conditions and boundaries accurately. Imladri saves these optional ratings with the account and wording edition for grouped results. You can withdraw them from the summary or Settings.</p>
      <p>Adults can opt in to ongoing questionnaire research. Imladri then uses the structured answers already saved to their account and any later edits to compare choices across Individual and Couples questionnaires, even when a questionnaire is unfinished. The report groups ages and heights into ranges. For numeric comparisons, it can summarize the relationship using the saved measurements without displaying an individual’s age or height. Staff can see aggregate comparisons, including partner-to-partner comparisons where both partners participate. The report withholds small groups. Personal descriptions, written reflections, private notes and conversation text are excluded.</p>
      <p>Adults can separately allow Imladri staff to download coded records for analysis. The Individual CSV includes saved structured questionnaire choices, including Sex &amp; Intimacy, skips, conditions and the profile categories provided, with age and height in ranges. A Couples CSV requires both current partners to agree. It can include each seat’s demographic categories, mutually shared starter choices, agreement milestones, shared check-in results and dates. Written messages, private drafts, free-text notes, names and email addresses stay outside these CSVs.</p>
      <p>When an adult allows both tone research and the corresponding CSV, new reads can contribute coded feedback on fit, sending and helpfulness. A shared Couples read requires both partners’ permissions for both features. Private draft coaching, suggested replies and written corrections stay outside the files.</p>
      <p>Research codes let staff follow a participating person or couple across downloads. Staff can link an Individual record to a Couples seat only when both partners have allowed both exports. Either partner can end the couple’s eligibility. Withdrawal affects future downloads. A previously downloaded copy may still exist. Couples permission applies to the current pair.</p>
      <p>You can turn ongoing participation off in Settings. Your answers then stop contributing when the Research report is next loaded. You can export your account records or clear saved content in Settings. Previously submitted standalone research contributions can still be managed there. A new ongoing research preference supersedes the older submission for reporting.</p>
      <p>You can blur a screenshot on your device before sending the preview to DeepSeek for transcription. Imladri saves the conversation text or outcome you submit and the advice it generates. The app does not save the screenshot itself. <a className="underline" href="https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html">DeepSeek Open Platform terms</a></p>
      <p>Adults participating in questionnaire research can separately volunteer to repeat the Basics. If you agree, Imladri saves your two study answers per question and their submission dates and publishes group agreement statistics once the thresholds are met. Remove the study entry in Personal or turn off questionnaire research in Settings to exclude it from future reports.</p>
      <h2>Hosting and storage</h2>
      <p>Cloudflare hosts the site and primary app API, and measures page views and load times. Render hosts PostgreSQL and supporting API services. Current app records are stored in PostgreSQL. Deleting a record removes it from the live application database; a separate backup can retain an earlier copy until it expires or is removed.</p>
      <h2>Billing</h2>
      <p>Eligible new accounts can select a monthly plan, verify a card with Stripe and explicitly authorize the card-first introduction before receiving ten free AI messages, shared across Individual and Couples. After the tenth free AI message is delivered, the selected plan is charged automatically, plus applicable tax, and its full paid allowance starts once payment is verified. Cancel the introduction in Plans before using all ten messages to prevent that first charge. Imladri records the selected plan, authorization version and time, card-setup status, usage and billing results. Your full card number stays with Stripe.</p>
      <p>Usage and grant records prevent duplicate introductory credits. To limit repeated claims, Imladri processes verified account identifiers, email addresses and Stripe’s card comparison identifier into keyed comparison records retained for up to one year. These comparison records do not contain your card number or questionnaire answers. Short-lived, keyed device and network signals help detect unusual claim activity. Cloudflare Turnstile checks setup requests for automated activity. Stripe may request card verification or additional authentication. Clearing content does not reset a message allowance or cancel billing.</p>
      <p>Stripe collects payment details on its checkout and billing pages. Imladri stores billing identifiers, subscription status, billing dates and usage so it can apply your plan. New purchases also retain the purchase policy version and wording you acknowledged, its date, the selected offer and credit-delivery records. These records help resolve billing questions. Private conversations, questionnaire answers, intimacy information and Your Type portrait choices are not used as payment-dispute evidence. Your full card number stays with the payment provider.</p>
      <p>For the Couples offer, Imladri records which accounts redeemed the sponsored seat, when the offer expires, and whether you authorized a future payment. Clearing Couples content keeps a record that the seat was used.</p>
      <p>Closing Personal removes its account-linked payment history and purchase acknowledgments from Imladri. Coded financial totals and verification markers can remain so account deletion does not hide an unresolved payment or make revenue appear verified. These retained totals and markers exclude direct account, contact and Stripe identifiers, questionnaire answers, conversations and purchase-policy wording. Stripe keeps its own payment records.</p>
      <h2>Support</h2>
      <p>A support request contains the topic and message you enter and any staff reply. Its private return link gives access to that request. You can delete it from that link, and requests expire after 90 days. Leave private conversations and payment credentials out of support messages.</p>
      <h2>Your choices</h2>
      <p>Open a person’s profile and choose Saved data to review or delete individual items. In Settings, you can export your Personal records, clear personal content or close Personal. Clearing content keeps your subscription active.</p>
      <p>Closing Personal deletes your Personal content and cancels Personal Plus. Your shared Imladri sign-in and Market access stay available. Imladri retains a one-way account reference to record the closure. Stripe keeps its own payment records.</p>
      <p>For a privacy or access question, <Link className="underline" to="/support?topic=Privacy">contact Imladri support</Link>.</p>
    </PublicPage>
  );
}

export function Billing() {
  return (
    <PublicPage eyebrow="Subscriptions" title="Billing" testid="billing-page">
      <h2>Dating founding beta</h2>
      <p>Dating enrollment is currently free and does not require a card or create an automatic dating subscription. Existing Individual and Couples purchases remain separate. If paid dating membership becomes available, its price and purchase terms will be shown before checkout. <Link className="underline" to="/plans">Dating beta details</Link>.</p>
      <h2>Existing private tool memberships</h2>
      <p>Each private tool membership includes the same Individual and Couples workspaces. Compare the monthly prices and reply allowances on the <Link className="underline" to="/legacy/plans">private tool Plans page</Link>. Your first payment starts your membership. Unused monthly replies expire at the end of your billing period.</p>
      <h2>Card-first introduction</h2>
      <p>Where offered to an eligible new account, choose your monthly plan and verify a card before starting ten free AI messages. These are ten messages total across Individual and Couples, not ten per workspace or per partner. The setup screen shows your selected plan, monthly price and authorization. No subscription payment is due at setup; Stripe or your bank may show a temporary verification authorization.</p>
      <p>By separately accepting the card-first authorization, you authorize the first monthly payment automatically after your tenth free AI message is delivered, plus applicable tax. Your paid billing month and full purchased message allowance start only after payment is verified. An unfinished or failed AI request does not itself spend an introductory message. A required card authentication or failed payment can pause paid access until resolved. Do not submit a second purchase while a payment is pending.</p>
      <p>To prevent the first charge, cancel the introduction in <Link className="underline" to="/app/settings/upgrade">Plans</Link> before using all ten messages and wait for cancellation to be confirmed. Cancellation ends the remaining introductory access; unused free messages have no cash value. After the first payment, monthly renewals continue until canceled. Accounts that are not eligible for the introduction can review the ordinary paid plans without being promised free messages.</p>
      <p>Plus members aged 18 or older can sponsor one adult partner in Couples. The partner joins with a separate sign-in. Each new AI coaching reply either of you requests uses one reply from your membership. Answering questions, sharing with each other and trying the styles in a saved reply are included.</p>
      <h2>Partner pricing</h2>
      <p>After the couple confirms the invitation, the eligible invited partner can add Individual for $10 per month. They then get their own 100 monthly replies across Individual and Couples. When both partners have memberships, each uses their own allowance. This rate applies to one confirmed pairing per account.</p>
      <p>The first successful $10 payment opens a 24-hour offer for both partners. Each can independently authorize one year of Plus for $80, due at the end of their current paid month. If both choose it, the total is $160 before any applicable tax. Each account gets 100 replies per month throughout the year. Access ends after that year with no automatic renewal.</p>
      <p>A first read, chat reply, practice turn, group read, game plan or new Couples AI coaching response uses one reply. All five writing styles come with that response. A new billing month starts a new allowance.</p>
      <h2 id="message-allowance" className="scroll-mt-28">Message allowance and refills</h2>
      <p>Your included replies are used first and reset each billing month. With an active membership, you can buy 100 extra replies for a one-time $12. Unused refill replies carry forward and can be used during an active membership. Refunding a refill reduces its credited replies in proportion to the refund. <Link className="underline" to="/support?topic=Refund">Request a refund</Link>.</p>
      <h2>Payments and renewal</h2>
      <p>Monthly memberships renew until canceled or switched to the one-year offer. Before you confirm a monthly upgrade, Stripe shows the price, any credit for unused time and what is due. A paid upgrade starts a new billing month. Review payment details and applicable tax in Stripe.</p>
      <h2>Cancel or change your payment details</h2>
      <p>If you scheduled the $80 year and change your mind before it starts, open Membership and select Cancel scheduled membership. This cancels the upcoming charge and ends your membership when your current paid month finishes.</p>
      <p>Sign in, open <Link className="underline" to="/app/settings/upgrade">Plans</Link>, then select Manage billing. Stripe shows your cancellation options and the date they take effect. Clearing personal content keeps your subscription active. Closing Personal cancels Personal Plus immediately. Your shared Imladri sign-in and Market access stay available.</p>
      <h2 id="refunds" className="scroll-mt-28">Charges and refunds</h2>
      <p>Imladri does not offer discretionary change-of-mind refunds once the purchased access or messages have been delivered. This includes an unused monthly allowance, unused delivered top-up messages, forgetting to cancel, or deciding not to use the service. Cancellation stops future renewal; it does not automatically refund earlier payments. AI suggestions are interpretations and do not guarantee a particular personal or relationship outcome.</p>
      <p>This policy does not limit mandatory consumer rights or Stripe/Link’s applicable refund policy. Billing errors, duplicate or unauthorized charges, failure to deliver, and a service materially different from what was sold remain eligible for review. Depending on your location, you may have statutory cancellation or withdrawal rights. Acknowledging our purchase terms or using some messages does not automatically waive those rights.</p>
      <p>For sales through Stripe Managed Payments, Link handles payment support and may issue refunds under its own policy, which takes precedence over a more restrictive seller policy. <a className="underline" href="https://support.link.com/questions/requesting-a-refund-for-a-sold-through-link-payment">Link refund policy and payment support</a>.</p>
      <p>To report a billing problem, include the invoice or receipt number and a short description. Do not send private conversation or questionnaire content. If a payment is refunded or a dispute is lost, the corresponding message entitlement is adjusted; an unrelated paid billing period is not charged again.</p>
      <p><Link className="underline" to="/support?topic=Refund">Report a refund issue</Link> · <Link className="underline" to="/support?topic=Billing">Get billing help</Link></p>
    </PublicPage>
  );
}

export function Terms() {
  return (
    <PublicPage eyebrow="Using the service" title="Terms" testid="terms-page">
      <p>These terms apply to your use of Imladri at imladri.com, including dating, the public library, and the existing private tools. The public library offers relationship archetypes and interpretations of public personas. The private tools generate suggestions based on details you share.</p>
      <h2>Dating community</h2>
      <p>Dating is for adults 18 and older. Provide accurate information about yourself, use photos you have permission to share, and respect consent and boundaries. Do not impersonate another person, use the service for scams, solicit money, harass others, or share someone’s private material without permission. You can block, unmatch, or report a concern in dating.</p>
      <p>Founding enrollment does not guarantee profile approval, available people in your area, a match, a date, or relationship success. Profiles and preference summaries are information users choose to share; they are not a guarantee of identity or a scientific prediction of compatibility. Dating has a separate enrollment and consent flow.</p>
      <h2>Using AI suggestions</h2>
      <p>AI can misread context or make factual mistakes. A suggestion is an interpretation, and you decide whether to act on it. Imladri does not provide professional medical, psychological or legal services or assess anyone’s private motives.</p>
      <h2>What you share</h2>
      <p>Use information you have permission to share. Respect other people’s privacy. Do not use the service to impersonate, harass or threaten someone, or attempt to access someone else’s account. Keep your sign-in credentials and private support links secure.</p>
      <h2>Paying for Plus</h2>
      <p>Monthly Plus renews until canceled. An eligible card-first introduction requires choosing a plan, verifying a card and separately authorizing the first monthly charge after ten free AI messages are delivered. You can cancel before that charge in Plans. An eligible partner’s one-year offer ends automatically. The <Link className="underline" to="/billing">billing page</Link> explains prices, reply allowances, cancellation and refund requests.</p>
      <p>The purchase policy applies to new purchases acknowledged under version imladri-purchases-2026-11-v1. Imladri does not offer change-of-mind refunds for delivered access or messages, including unused allowances. Mandatory consumer rights and the applicable Stripe/Link refund policy take precedence. This does not change the terms of an earlier purchase retroactively.</p>
      <h2>Your data and account</h2>
      <p>The <Link className="underline" to="/privacy">privacy notice</Link> explains what Imladri sends to its providers, what the app saves and how to export or delete records. For account or service questions, <Link className="underline" to="/support">contact support</Link>.</p>
    </PublicPage>
  );
}
