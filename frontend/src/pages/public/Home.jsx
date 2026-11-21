import { HeroStage } from "@/components/public/HeroStage";

export default function Home() {
  return (
    <section className="home-walking-hero relative" aria-labelledby="home-hero-title" data-testid="home-hero">
      <div className="grain absolute inset-0 pointer-events-none z-40" aria-hidden />
      <HeroStage />
    </section>
  );
}
