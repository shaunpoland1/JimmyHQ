import { AppWindow, BookOpen, Calendar, Camera, ChartColumn, Heart, House, Image, Mail, Megaphone, Mic, Newspaper, Package, Pen, Truck, Users, Wallet } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { AppIconName } from "./lib/installed";

/** The pictures the shell draws for an app's sidebar line. An app only names one of these. */
const APP_ICONS: Record<AppIconName, LucideIcon> = {
  book: BookOpen,
  calendar: Calendar,
  camera: Camera,
  chart: ChartColumn,
  heart: Heart,
  home: House,
  image: Image,
  mail: Mail,
  megaphone: Megaphone,
  mic: Mic,
  newspaper: Newspaper,
  package: Package,
  pen: Pen,
  truck: Truck,
  users: Users,
  wallet: Wallet,
};

/** The picture for an app's line: the one it chose, else the usual window. */
export function appIcon(name: AppIconName | null): LucideIcon {
  return name ? APP_ICONS[name] : AppWindow;
}
