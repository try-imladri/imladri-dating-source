import { Silhouette } from "@/components/public/Silhouette";
import { LANDING_HEADLINE, LANDING_SUMMARY } from "./landingCopy";
import "./WalkingHero.css";

export function HeroStage() {
  return <div className="walking-hero" data-testid="home-stage">
    <div className="walking-hero-content">
      <div className="walking-hero-panel" data-testid="home-feature-container">
        <div className="walking-hero-people" aria-hidden="true" data-testid="home-walking-scene">
          <Silhouette src="/img/couple-walk.webp" className="walking-hero-couple" testid="home-original-walking-couple" />
          <span className="walking-hero-ground" />
        </div>
        <div className="walking-hero-message" data-testid="home-feature-story">
          <p className="walking-hero-eyebrow">Intentional dating · Founding beta · 18+</p>
          <h1 id="home-hero-title" className="walking-hero-title font-display font-black uppercase" data-testid="home-hero-title">{LANDING_HEADLINE}</h1>
          <p className="walking-hero-summary" data-testid="home-product-summary">{LANDING_SUMMARY}</p>
          <div className="walking-hero-actions">
            <a href="/dating?origin=home" className="btn-lime px-6 py-3" data-testid="home-dating-cta">Join the founding community</a>
            <a href="/how-it-works" className="walking-hero-secondary">How it works</a>
          </div>
          <p className="walking-hero-availability">We’re building the community. Introductions depend on who joins in your area.</p>
        </div>
      </div>
    </div>
  </div>;
}
