import { createContext, useContext, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const ThemeCtx = createContext({ theme: "light", toggle: () => {} });

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("imaldri_theme") || "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try { localStorage.setItem("imaldri_theme", theme); } catch { /* The selection lasts for this visit. */ }
  }, [theme]);
  return <ThemeCtx.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button onClick={toggle} aria-label="Toggle light/dark mode" data-testid="theme-toggle"
      className="w-9 h-9 rounded-full border border-[var(--line)] flex items-center justify-center hover:border-[var(--ink)] transition-colors">
      {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
}
