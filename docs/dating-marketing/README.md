# Imladri dating launch kit

Prepared 19 November 2026. These are production drafts, not published posts or evidence of market demand.

## One service and one promise

**Meet your type. Understand your fit.**

Imladri is an intentional dating community built around attraction, relationship preferences, and what each person chooses to share. Personality questions, optional intimacy preferences, and Your Type support that service. The first public call to action is **Join the founding community**. No claims of proven chemistry, guaranteed compatibility, a verified member pool, or dates already happening.

The initial audience hypothesis is adults seeking a committed relationship in one launch city. Chicago and ages 30–45 are test choices, not validated findings. Enrollment remains 18+. Before local creative runs, Alex and the founder must choose a city they can recruit in, establish the reciprocal preference groups they can actually serve, and use the same location throughout the campaign. Do not invent a local launch or imply exclusivity based on protected traits. Never make the audience feel defective for being single.

## What is ready

- [16 scripts with captions and CTAs](tiktok-scripts.md): eight fictional scenarios, four product demos, and four founder/community/privacy posts.
- [Four-week calendar and measurement plan](four-week-calendar.md).
- [Archetype, public-figure, AI, and platform rules](editorial-and-platform-rules.md).
- Original social-preview artwork: `frontend/public/img/imladri-dating-social-v1.svg` and `.png`. Rebuild using `python docs/dating-marketing/build_social_art.py` with Pillow and fontTools; it uses the repository’s bundled fonts and leaves the earlier artwork intact.

These files are a launch kit for recording and review. No posts, outreach messages, ad purchases, or public-figure assets have been published or licensed by this implementation.

## Keep the product promise aligned with reality

| Product state | Allowed CTA | Avoid |
| --- | --- | --- |
| Recruiting / founding intake | Join the founding community at imladri.com | Meet available singles tonight; browse hundreds of members |
| Reviewed profiles exist but a local reciprocal pool is thin | Join the founding community; introductions depend on local participation | Your matches are waiting; a fixed count of nearby people |
| Discovery is genuinely available for the audience shown | Join Imladri and explore who is available to you | Guaranteed matches, guaranteed dates, scientific chemistry |
| Paid dating membership is actually enabled with approved purchase terms | Review the displayed membership price and terms | A $19 launch claim merely because $19 was a research hypothesis |

Every profile demo must display **Fictional demo · not a member** throughout. Hide real names, photos, messages, locations, consent choices, and contact details. Record with an isolated demonstration account and synthetic content. Existing private-workspace data is never marketing material by default.

## Navigation and preservation

The main public message is dating. `Learn` contains the archetype and public-figure libraries as editorial material. Existing routes and records stay in place. `/legacy` is the private-tool directory; `/legacy/plans` preserves the original paid plan catalog; `/legacy/how-it-works` preserves the earlier walkthrough. Individual, Couples, questionnaires, Your Type, practice, and settings remain at their existing `/app/...` routes. This directory is not a migration or publication of old content.

The public founding offer is free dating enrollment without a card or automatic dating subscription. Old memberships continue under their existing billing terms; the dating beta must not obscure cancellation or billing management for those customers.

## Roles and a useful launch decision

The founder owns product truth, privacy controls, and demonstrations. Alex owns introductions to local communities and creators, a documented recruitment pipeline, and conversations with people who can bring real eligible participants. Content is useful only if it brings adults who complete profiles and have reciprocal interest in the same area.

At the end of four weeks, review the measured funnel in the calendar. Continue a narrow local cohort if it shows reciprocal interaction. Change the offer or recruitment channel if attention produces few completed profiles. Delay expansion if existing groups have no plausible pool. Do not add Couples, professional practice, or a second city simply to create more content.
