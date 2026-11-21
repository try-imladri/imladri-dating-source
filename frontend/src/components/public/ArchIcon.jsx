import { Bomb, HeartOff, Cookie, Briefcase, Map, Gift, Ghost, Skull, Feather, Dog, Scale, Cpu, Thermometer, Brain, ShieldAlert, Home, Shuffle, Bird, Flame, Pickaxe, CalendarClock, Sparkles, Wrench, Luggage, Camera, Armchair, Gavel, Lock, Swords, Gamepad2, Smartphone, Radio, ClipboardList, Zap, CloudFog, Candy, Laptop, CloudRain, Drama, BookOpen, Repeat, Eye, Puzzle, Wallet, Hourglass, BadgeCheck, Shapes,
  Glasses, Hand, Tornado, Snowflake, Heart, Orbit, EyeOff, Undo2, Hammer, LifeBuoy, PartyPopper, Video, BriefcaseBusiness, Frown, PenLine, ScanSearch, Baby, HeartCrack, Bandage, FlameKindling, Contact, Gem, Wind, TrendingUp, Crown, Archive, Megaphone, BookLock, Castle, Egg, MessagesSquare, Infinity, Receipt, Rocket, Theater, HeartHandshake, Timer, CloudDrizzle, Building2, Sun, RefreshCcw, Binoculars, Users, HandHeart, Sunset, ShieldCheck, FlaskConical, Sprout, Moon, Palette } from "lucide-react";

// One symbol per archetype in roster order: male #1-#47, female #101-#150.
const MALE = [Bomb, HeartOff, Cookie, Briefcase, Map, Gift, Ghost, Skull, Feather, Dog, Scale, Cpu, Thermometer, Brain, ShieldAlert, Home, Shuffle, Bird, Flame, Pickaxe, CalendarClock, Sparkles, Wrench, Luggage, Camera, Armchair, Gavel, Lock, Swords, Gamepad2, Smartphone, Radio, ClipboardList, Zap, CloudFog, Candy, Laptop, CloudRain, Drama, BookOpen, Repeat, Eye, Puzzle, Wallet, Hourglass, BadgeCheck, Shapes];
const FEMALE = [Glasses, Hand, Tornado, Snowflake, Heart, Orbit, EyeOff, Undo2, Hammer, LifeBuoy, PartyPopper, Video, BriefcaseBusiness, Frown, PenLine, ScanSearch, Baby, HeartCrack, Bandage, FlameKindling, Contact, Gem, Wind, TrendingUp, Crown, Archive, Megaphone, BookLock, Castle, Egg, MessagesSquare, Infinity, Receipt, Rocket, Theater, HeartHandshake, Timer, CloudDrizzle, Building2, Sun, RefreshCcw, Binoculars, Users, HandHeart, Sunset, ShieldCheck, FlaskConical, Sprout, Moon, Palette];

export function ArchIcon({ number, className = "w-6 h-6", stroke = 1.75 }) {
  const newIndex = number > 100 ? number - 151 : number - 48;
  const communication = [MessagesSquare, Hammer, HeartHandshake, HandHeart, BookOpen, RefreshCcw, Megaphone, FlaskConical, Theater];
  const I = (number > 100 ? FEMALE[number - 101] : MALE[number - 1]) || communication[newIndex] || Shapes;
  return <I className={className} strokeWidth={stroke} data-testid={`arch-icon-${number}`} />;
}
