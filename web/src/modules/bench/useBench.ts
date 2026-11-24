import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { ApiError, errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { benchApi } from "./api";
import type { Action, Board, Card, CardListItem } from "./api";
import { decideBody, findCard, placeCard, predictDecision, predictSnooze, predictUnsnooze, toListItem } from "./lib/board";
import { subscribeBenchFeed } from "./live";

export const BENCH_BOARD_KEY = ["bench", "board"] as const;
export const benchCardKey = (id: string) => ["bench", "card", id] as const;

// A snoozed card comes back without any event, so the board is also re-read
// now and then. Everything else arrives through the live feed.
const SLOW_REFRESH_MS = 60_000;
const FEED_SETTLE_MS = 120;

/** The Bench lanes and counts, kept current by the live feed. */
export function useBenchBoard() {
  const client = useQueryClient();

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refreshBoard = () => {
      // Several events often land together (a decision and what it causes).
      clearTimeout(timer);
      timer = setTimeout(() => void client.invalidateQueries({ queryKey: BENCH_BOARD_KEY }), FEED_SETTLE_MS);
    };
    const stop = subscribeBenchFeed({
      onEvent: (event) => {
        refreshBoard();
        const cardId = event.payload.card_id;
        if (typeof cardId === "string") void client.invalidateQueries({ queryKey: benchCardKey(cardId) });
      },
      onReconnect: () => void client.invalidateQueries({ queryKey: ["bench"] }),
    });
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [client]);

  return useQuery({ queryKey: BENCH_BOARD_KEY, queryFn: benchApi.board, refetchInterval: SLOW_REFRESH_MS });
}

/** One card in full, loaded by id. Works for closed cards too. */
export function useBenchCard(id: string | null) {
  return useQuery({
    queryKey: benchCardKey(id ?? ""),
    queryFn: () => benchApi.card(id ?? ""),
    enabled: id !== null,
  });
}

export type ActionResult = { ok: true; card: Card } | { ok: false; status: number; detail: string };

type CardRef = Pick<CardListItem, "id">;

export type BenchActions = {
  /** `card` is the card as it is on screen: its `updates` tells the server which revision was read. */
  decide: (card: CardRef & Pick<CardListItem, "updates">, action: Action, note: string, reason: string) => Promise<ActionResult>;
  /** Without `until` the server uses its default snooze length. */
  snooze: (card: CardRef, until?: number) => Promise<ActionResult>;
  unsnooze: (card: CardRef) => Promise<ActionResult>;
};

/**
 * The owner's clicks. Each shows its effect on the lists at once, and puts
 * things back with a toast if the server refuses.
 */
export function useBenchActions(): BenchActions {
  const client = useQueryClient();
  const toast = useToast();

  return useMemo(() => {
    const run = async (
      id: string,
      predict: (current: CardListItem) => CardListItem | null,
      send: () => Promise<Card>,
    ): Promise<ActionResult> => {
      // A read already on its way would bring back the list from before the click.
      await client.cancelQueries({ queryKey: BENCH_BOARD_KEY });
      const before = client.getQueryData<Board>(BENCH_BOARD_KEY);
      const current = before ? findCard(before, id) : undefined;
      if (before && current) client.setQueryData<Board>(BENCH_BOARD_KEY, placeCard(before, id, predict(current)));
      try {
        const card = await send();
        client.setQueryData<Card>(benchCardKey(id), card);
        client.setQueryData<Board>(BENCH_BOARD_KEY, (board) => (board ? placeCard(board, id, toListItem(card)) : board));
        return { ok: true, card };
      } catch (error) {
        if (before) client.setQueryData<Board>(BENCH_BOARD_KEY, before);
        // The card may have changed under us (closed elsewhere): read it again.
        void client.invalidateQueries({ queryKey: benchCardKey(id) });
        const detail = errorDetail(error);
        toast.error(detail);
        return { ok: false, status: error instanceof ApiError ? error.status : 0, detail };
      } finally {
        void client.invalidateQueries({ queryKey: BENCH_BOARD_KEY });
      }
    };

    return {
      decide: (card, action, note, reason) =>
        run(
          card.id,
          (current) => predictDecision(current, action, Date.now()),
          () => benchApi.decide(card.id, decideBody(card, action, note, reason)),
        ),
      snooze: (card, until) =>
        run(
          card.id,
          // The server picks the time when none is given; any future time lists the card the same way.
          (current) => predictSnooze(current, until ?? Date.now() + 60_000),
          () => benchApi.snooze(card.id, until),
        ),
      unsnooze: (card) => run(card.id, (current) => predictUnsnooze(current, Date.now()), () => benchApi.unsnooze(card.id)),
    };
  }, [client, toast]);
}
