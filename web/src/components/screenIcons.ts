import { AppWindow, Bot, Brain, Inbox, LayoutGrid, ListTodo, MessageSquare, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ScreenId } from "../lib/screens";

export const SCREEN_ICONS: Record<ScreenId, LucideIcon> = {
  bench: Inbox,
  tasks: ListTodo,
  chat: MessageSquare,
  village: Bot,
  memory: Brain,
  apps: LayoutGrid,
  settings: Settings,
  app: AppWindow,
};
