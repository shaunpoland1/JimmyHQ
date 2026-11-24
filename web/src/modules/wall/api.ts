// The Wall has no endpoints of its own: it reads what the other modules
// already serve, through their own typed clients. This file names the calls
// it makes and the cache keys it keeps their answers under.

import { api } from "../../api.ts";
import type { TapeResponse, TapeStats } from "../../api.ts";
import { benchApi } from "../bench/api.ts";
import type { Board } from "../bench/api.ts";
import { modelsApi } from "../models/api.ts";
import type { HealthResponse, UsageResponse } from "../models/api.ts";
import { tasksApi } from "../tasks/api.ts";
import type { TaskBoard } from "../tasks/api.ts";

export type { Board, HealthResponse, TapeResponse, TapeStats, TaskBoard, UsageResponse };

export const WALL_KEY = ["wall"] as const;

export const WALL_KEYS = {
  usage: ["wall", "usage"] as const,
  tape: ["wall", "tape"] as const,
  bench: ["wall", "bench"] as const,
  today: ["wall", "today"] as const,
  models: ["wall", "models"] as const,
  feed: ["wall", "feed"] as const,
};

export const wallApi = {
  /** GET /api/models/usage */
  usage: (): Promise<UsageResponse> => modelsApi.usage(),
  /** GET /api/models/health */
  health: (): Promise<HealthResponse> => modelsApi.health(),
  /** GET /api/tape/stats */
  tapeStats: (): Promise<TapeStats> => api.tapeStats(),
  /** GET /api/bench */
  bench: (): Promise<Board> => benchApi.board(),
  /** GET /api/tasks for the owner, every area. An agent's own chores are not the owner's to do. */
  tasks: (): Promise<TaskBoard> => tasksApi.board({ assignee: "owner", area: null }),
  /** GET /api/tape: the newest events, newest first. */
  recent: (limit: number): Promise<TapeResponse> => api.tape({ limit }),
};
