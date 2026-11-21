import { useOutletContext } from "react-router-dom";
import YourType from "@/components/app/YourType";

export default function YourTypePage() {
  const { me, api } = useOutletContext();
  return <div className="space-y-4" data-testid="your-type-page">
    <header>
      <h1 className="sr-only sm:not-sr-only font-display font-black uppercase text-4xl sm:text-5xl leading-none">Your Type</h1>
      <p className="hidden sm:block text-sm text-[var(--muted)] mt-2">Explore the visual patterns in who catches your eye.</p>
    </header>
    <YourType api={api} owner={me.user_id} />
  </div>;
}
