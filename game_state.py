from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any

from news_engine import NewsEngine


class Phase(str, Enum):
    LOBBY           = "lobby"
    PHASE1_REVEAL   = "phase1_reveal"
    PHASE1_TRADING  = "phase1_trading"
    PHASE2_REVEAL   = "phase2_reveal"
    PHASE2_TRADING  = "phase2_trading"
    ROUND_RESULTS   = "round_results"
    FINISHED        = "finished"


class CommandType(str, Enum):
    JOIN          = "join"
    START_GAME    = "start_game"
    PLAYER_ACTION = "player_action"


@dataclass
class Command:
    type:    CommandType
    game_id: str
    sid:     str
    payload: dict


@dataclass
class Player:
    name:      str
    role:      str   = "player"
    cash:      float = 1000.0
    connected: bool  = True
    sid:       str | None = None
    eliminated: bool = False

    # Reset each round
    round_position: int   = 0
    round_pnl:      float = 0.0
    phase1_pnl:     float = 0.0
    phase2_pnl:     float = 0.0
    acted_phase1:   bool  = False
    acted_phase2:   bool  = False


@dataclass
class TradeRecord:
    phase:       int
    player_name: str
    action:      str
    qty:         int
    price:       float | None
    signed_qty:  int          # +ve = long, -ve = short
    cashflow:    float        # actual cash movement at trade time (not used for settlement)


@dataclass
class RoundState:
    round_index: int
    all_cards:   list[int] = field(default_factory=list)

    # Cards visible to players
    revealed_cards: list[int] = field(default_factory=list)

    # News layers
    deck_news:   list[dict] = field(default_factory=list)   # carried-over persistent news
    phase1_news: list[dict] = field(default_factory=list)
    phase2_news: list[dict] = field(default_factory=list)

    # Market prices at each stage
    market_price_phase1_pre_news:    float | None = None
    market_price_phase1_frozen:      float | None = None
    market_price_phase2_pre_news:    float | None = None
    market_price_phase2_frozen:      float | None = None

    # Trade records per phase
    phase1_trades: list[TradeRecord] = field(default_factory=list)
    phase2_trades: list[TradeRecord] = field(default_factory=list)

    # Settlement
    true_value: float | None = None

    # Valid value sets used to compute each market price
    valid_values_phase1_pre_news:  list[int] = field(default_factory=list)
    valid_values_phase1_frozen:    list[int] = field(default_factory=list)
    valid_values_phase2_pre_news:  list[int] = field(default_factory=list)
    valid_values_phase2_frozen:    list[int] = field(default_factory=list)

    # Deck news announced at end of this round
    announced_deck_news_end_of_round: dict | None = None


@dataclass
class GameState:
    game_id:      str
    config:       dict
    news_engine:  NewsEngine

    phase:        Phase = Phase.LOBBY
    players:      dict[str, Player] = field(default_factory=dict)
    sid_to_name:  dict[str, str]    = field(default_factory=dict)

    round_index:   int              = 0
    current_round: RoundState | None = None
    phase_ends_at: float | None     = None

    # Deck news accumulates across rounds
    persistent_deck_news: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def active_players(self) -> list[Player]:
        return [p for p in self.players.values() if not p.eliminated]

    def leaderboard(self) -> list[dict]:
        ranked = sorted(
            self.players.values(),
            key=lambda p: (not p.eliminated, p.cash),
            reverse=True,
        )
        out = []
        for i, p in enumerate(ranked, start=1):
            out.append({
                "rank":       i,
                "name":       p.name,
                "cash":       round(p.cash, 2),
                "round_pnl":  round(p.round_pnl, 2),
                "phase1_pnl": round(p.phase1_pnl, 2),
                "phase2_pnl": round(p.phase2_pnl, 2),
                "eliminated": p.eliminated,
            })
        return out

    # ------------------------------------------------------------------ #
    # State snapshots
    # ------------------------------------------------------------------ #

    def public_state(self) -> dict[str, Any]:
        """JSON-safe snapshot broadcast to all clients."""
        round_data = None
        if self.current_round:
            r = self.current_round
            # Only expose phase2 news once phase2 is active
            show_p2_news = self.phase in (
                Phase.PHASE2_TRADING, Phase.ROUND_RESULTS, Phase.FINISHED
            )
            round_data = {
                "round_index":    r.round_index,
                "revealed_cards": list(r.revealed_cards),
                "phase1_news":    r.phase1_news,
                "phase2_news":    r.phase2_news if show_p2_news else [],
                "deck_news":      list(self.persistent_deck_news),
                # Market prices — all four stages
                "market_price_phase1_pre_news":  r.market_price_phase1_pre_news,
                "market_price_phase1_frozen":    r.market_price_phase1_frozen,
                "market_price_phase2_pre_news":  r.market_price_phase2_pre_news,
                "market_price_phase2_frozen":    r.market_price_phase2_frozen,
                # Trades
                "phase1_trades": [self._trade_to_dict(t) for t in r.phase1_trades],
                "phase2_trades": [self._trade_to_dict(t) for t in r.phase2_trades],
                # True value only revealed after round ends
                "true_value": r.true_value if self.phase in (Phase.ROUND_RESULTS, Phase.FINISHED) else None,
                "announced_deck_news_end_of_round": (
                    r.announced_deck_news_end_of_round
                    if self.phase in (Phase.ROUND_RESULTS, Phase.FINISHED) else None
                ),
            }

        return {
            "game_id":              self.game_id,
            "phase":                self.phase.value,
            "round_index":          self.round_index,
            "config":               self.config,
            "round":                round_data,
            "players":              [
                {
                    "name":           p.name,
                    "role":           p.role,
                    "cash":           round(p.cash, 2),
                    "round_position": p.round_position,
                    "round_pnl":      round(p.round_pnl, 2),
                    "phase1_pnl":     round(p.phase1_pnl, 2),
                    "phase2_pnl":     round(p.phase2_pnl, 2),
                    "eliminated":     p.eliminated,
                }
                for p in self.players.values()
            ],
            "leaderboard":          self.leaderboard(),
            "phase_ends_at":        self.phase_ends_at,
            "server_time":          time.time(),
            "persistent_deck_news": list(self.persistent_deck_news),
        }

    def player_state(self, player_name: str) -> dict[str, Any]:
        """Personalised snapshot — adds player-specific fields."""
        base = self.public_state()
        p = self.players.get(player_name)
        base["player_data"] = {
            "my_name":           player_name,
            "my_cash":           round(p.cash, 2) if p else 0.0,
            "my_round_position": p.round_position if p else 0,
            "my_round_pnl":      round(p.round_pnl, 2) if p else 0.0,
            "my_phase1_pnl":     round(p.phase1_pnl, 2) if p else 0.0,
            "my_phase2_pnl":     round(p.phase2_pnl, 2) if p else 0.0,
            "eliminated":        p.eliminated if p else False,
            "ui_mode":           self._player_ui_mode(player_name),
        }
        return base

    def _player_ui_mode(self, player_name: str) -> str:
        p = self.players.get(player_name)
        if not p:
            return "lobby"
        if p.eliminated:
            return "final_results" if self.phase == Phase.FINISHED else "eliminated"

        mapping = {
            Phase.LOBBY:          "lobby",
            Phase.PHASE1_REVEAL:  "phase1_reveal",
            Phase.PHASE2_REVEAL:  "phase2_reveal",
            Phase.ROUND_RESULTS:  "round_results",
            Phase.FINISHED:       "final_results",
        }
        if self.phase in mapping:
            return mapping[self.phase]
        if self.phase == Phase.PHASE1_TRADING:
            return "phase1_acted" if p.acted_phase1 else "phase1_trade"
        if self.phase == Phase.PHASE2_TRADING:
            return "phase2_acted" if p.acted_phase2 else "phase2_trade"
        return "lobby"

    # ------------------------------------------------------------------ #
    # Command handler
    # ------------------------------------------------------------------ #

    def apply_command(self, cmd: Command) -> tuple[bool, list[dict]]:
        events: list[dict] = []

        # ── JOIN ────────────────────────────────────────────────────────
        if cmd.type == CommandType.JOIN:
            name = (cmd.payload.get("name") or "").strip()
            role = cmd.payload.get("role", "player")
            if not name or self.phase != Phase.LOBBY:
                return False, []
            if name in self.players:
                return False, []
            max_p = int(self.config.get("max_players", 100))
            if len(self.players) >= max_p:
                return False, []
            role = role if role == "host" else "player"
            self.players[name] = Player(name=name, role=role, sid=cmd.sid)
            self.sid_to_name[cmd.sid] = name
            events.append({"type": "player_joined", "player": {"name": name, "role": role}})
            return True, events

        # ── START GAME ──────────────────────────────────────────────────
        if cmd.type == CommandType.START_GAME:
            if self.phase != Phase.LOBBY:
                return False, []
            starter = self.sid_to_name.get(cmd.sid)
            if not starter or self.players[starter].role != "host":
                return False, []
            min_p = int(self.config.get("min_players", 2))
            if len(self.players) < min_p:
                return False, []
            self.start_next_round()
            events.append({"type": "round_started", "round_index": self.round_index})
            return True, events

        # ── PLAYER ACTION ───────────────────────────────────────────────
        if cmd.type == CommandType.PLAYER_ACTION:
            if self.phase not in (Phase.PHASE1_TRADING, Phase.PHASE2_TRADING):
                return False, []
            name = self.sid_to_name.get(cmd.sid)
            if not name or name not in self.players:
                return False, []
            player = self.players[name]
            if player.eliminated:
                return False, []

            action = (cmd.payload.get("action") or "pass").lower()
            qty    = int(cmd.payload.get("qty") or 0)
            limit  = int(self.config.get("phase_unit_limit", 20))

            if action not in ("buy", "sell", "pass"):
                action = "pass"
            if action in ("buy", "sell"):
                qty = max(0, min(qty, limit))
                if qty == 0:
                    action = "pass"
            else:
                qty = 0

            if self.phase == Phase.PHASE1_TRADING:
                if player.acted_phase1:
                    return False, []
                trade = self._execute_trade(player, action, qty, phase_num=1)
                player.acted_phase1 = True
                self.current_round.phase1_trades.append(trade)
                events.append(self._action_event(trade))
                if self._all_acted(phase_num=1):
                    self._start_phase2_reveal()
                return True, events

            if self.phase == Phase.PHASE2_TRADING:
                if player.acted_phase2:
                    return False, []
                trade = self._execute_trade(player, action, qty, phase_num=2)
                player.acted_phase2 = True
                self.current_round.phase2_trades.append(trade)
                events.append(self._action_event(trade))
                if self._all_acted(phase_num=2):
                    self.end_round()
                    events.append(self._round_ended_event())
                return True, events

        return False, []

    # ------------------------------------------------------------------ #
    # Tick (timer-driven transitions)
    # ------------------------------------------------------------------ #

    def tick(self) -> tuple[bool, list[dict]]:
        if not self.phase_ends_at or time.time() < self.phase_ends_at:
            return False, []

        events: list[dict] = []

        if self.phase == Phase.PHASE1_REVEAL:
            self.phase = Phase.PHASE1_TRADING
            self.phase_ends_at = time.time() + int(self.config.get("phase1_seconds", 30))
            return True, events

        if self.phase == Phase.PHASE1_TRADING:
            self._auto_pass(phase_num=1)
            self._start_phase2_reveal()
            return True, events

        if self.phase == Phase.PHASE2_REVEAL:
            self.phase = Phase.PHASE2_TRADING
            self.phase_ends_at = time.time() + int(self.config.get("phase2_seconds", 30))
            return True, events

        if self.phase == Phase.PHASE2_TRADING:
            self._auto_pass(phase_num=2)
            self.end_round()
            events.append(self._round_ended_event())
            return True, events

        if self.phase == Phase.ROUND_RESULTS:
            if self._should_finish():
                self.phase = Phase.FINISHED
                self.phase_ends_at = None
                events.append({"type": "game_finished", "leaderboard": self.leaderboard()})
            else:
                self.start_next_round()
                events.append({"type": "round_started", "round_index": self.round_index})
            return True, events

        return False, []

    # ------------------------------------------------------------------ #
    # Round lifecycle
    # ------------------------------------------------------------------ #

    def start_next_round(self):
        self.round_index += 1

        # Reset per-round player state
        for p in self.players.values():
            p.round_position = 0
            p.round_pnl      = 0.0
            p.phase1_pnl     = 0.0
            p.phase2_pnl     = 0.0
            p.acted_phase1   = False
            p.acted_phase2   = False

        # Deal 5 cards; reveal first 3
        cards    = self.news_engine.draw_cards(5)
        revealed = cards[:3]

        self.current_round = RoundState(
            round_index=self.round_index,
            all_cards=cards,
            revealed_cards=list(revealed),
            deck_news=list(self.persistent_deck_news),
        )

        # ── Phase 1 market prices ────────────────────────────────────────
        # Pre-news: only deck news constrains hidden values
        valid_pre = self.news_engine.compute_valid_totals(
            cards=cards,
            revealed_cards=revealed,
            active_news=self.persistent_deck_news,
        )
        self.current_round.valid_values_phase1_pre_news  = valid_pre
        self.current_round.market_price_phase1_pre_news  = self._ev(valid_pre)

        # Pick phase-1 news items (true for these cards)
        phase1_news = self.news_engine.pick_round_news(
            cards=cards,
            revealed_cards=revealed,
            active_news=self.persistent_deck_news,
            count=2,
        )
        self.current_round.phase1_news = phase1_news

        # Frozen: deck news + phase1 news applied
        valid_frozen = self.news_engine.compute_valid_totals(
            cards=cards,
            revealed_cards=revealed,
            active_news=self.persistent_deck_news + phase1_news,
        )
        self.current_round.valid_values_phase1_frozen = valid_frozen
        self.current_round.market_price_phase1_frozen = self._ev(valid_frozen)

        self.phase        = Phase.PHASE1_REVEAL
        self.phase_ends_at = time.time() + int(self.config.get("reveal_seconds", 8))

    def _start_phase2_reveal(self):
        """Reveal 4th card, compute phase-2 market prices, pick phase-2 news."""
        r = self.current_round
        if not r:
            return

        # Reveal 4th card
        if len(r.revealed_cards) < 4:
            r.revealed_cards = list(r.all_cards[:4])

        all_news_so_far = self.persistent_deck_news + r.phase1_news

        # Pre-news for phase 2: deck + phase1 news, now with 4 revealed cards
        valid_pre = self.news_engine.compute_valid_totals(
            cards=r.all_cards,
            revealed_cards=r.revealed_cards,
            active_news=all_news_so_far,
        )
        r.valid_values_phase2_pre_news  = valid_pre
        r.market_price_phase2_pre_news  = self._ev(valid_pre)

        # Pick phase-2 news
        phase2_news = self.news_engine.pick_round_news(
            cards=r.all_cards,
            revealed_cards=r.revealed_cards,
            active_news=all_news_so_far,
            count=2,
        )
        r.phase2_news = phase2_news

        # Frozen price: deck + phase1 + phase2 news
        valid_frozen = self.news_engine.compute_valid_totals(
            cards=r.all_cards,
            revealed_cards=r.revealed_cards,
            active_news=all_news_so_far + phase2_news,
        )
        r.valid_values_phase2_frozen = valid_frozen
        r.market_price_phase2_frozen = self._ev(valid_frozen)

        self.phase        = Phase.PHASE2_REVEAL
        self.phase_ends_at = time.time() + int(self.config.get("reveal_seconds", 8))

    def end_round(self):
        """
        Settlement logic:
          - True value = sum of all 5 cards
          - Phase 1 PnL = (true_value - phase1_frozen_price) * net_phase1_qty
          - Phase 2 PnL = (true_value - phase2_frozen_price) * net_phase2_qty
          - Round PnL   = phase1_pnl + phase2_pnl
          - Cash updated by round_pnl (not by cashflow at trade time)
        """
        r = self.current_round
        if not r:
            self.phase        = Phase.ROUND_RESULTS
            self.phase_ends_at = time.time() + int(self.config.get("results_seconds", 10))
            return

        true_value = sum(r.all_cards)
        r.true_value       = true_value
        r.revealed_cards   = list(r.all_cards)  # reveal all cards

        p1_price = r.market_price_phase1_frozen or 0.0
        p2_price = r.market_price_phase2_frozen or 0.0

        # Aggregate net quantities per player per phase
        p1_qty: dict[str, int] = {}
        p2_qty: dict[str, int] = {}

        for t in r.phase1_trades:
            p1_qty[t.player_name] = p1_qty.get(t.player_name, 0) + t.signed_qty
        for t in r.phase2_trades:
            p2_qty[t.player_name] = p2_qty.get(t.player_name, 0) + t.signed_qty

        # Settle each player
        for p in self.players.values():
            q1 = p1_qty.get(p.name, 0)
            q2 = p2_qty.get(p.name, 0)

            p.phase1_pnl   = round((true_value - p1_price) * q1, 2)
            p.phase2_pnl   = round((true_value - p2_price) * q2, 2)
            p.round_pnl    = round(p.phase1_pnl + p.phase2_pnl, 2)
            p.cash         = round(p.cash + p.round_pnl, 2)
            p.round_position = q1 + q2  # net position held at settlement

            if p.cash <= 0:
                p.eliminated = True

        # Pick one new persistent deck news item (may be None)
        deck_news = self.news_engine.pick_persistent_deck_news(
            cards=r.all_cards,
            active_news=self.persistent_deck_news,
        )
        r.announced_deck_news_end_of_round = deck_news
        if deck_news:
            self.persistent_deck_news.append(deck_news)

        self.phase        = Phase.ROUND_RESULTS
        self.phase_ends_at = time.time() + int(self.config.get("results_seconds", 10))

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _execute_trade(self, player: Player, action: str, qty: int, phase_num: int) -> TradeRecord:
        """
        Record a trade. We DO NOT move cash at trade time — settlement
        happens in end_round() based on (true_value - frozen_price) * qty.
        We still track signed_qty for position display.
        """
        r = self.current_round
        price = (
            (r.market_price_phase1_frozen if phase_num == 1 else r.market_price_phase2_frozen)
            if r else None
        )

        if action == "pass":
            return TradeRecord(phase_num, player.name, "pass", 0, None, 0, 0.0)

        signed_qty = qty if action == "buy" else -qty
        player.round_position += signed_qty

        return TradeRecord(
            phase=phase_num,
            player_name=player.name,
            action=action,
            qty=qty,
            price=float(price or 0.0),
            signed_qty=signed_qty,
            cashflow=0.0,   # settled at end of round, not here
        )

    def _auto_pass(self, phase_num: int):
        r = self.current_round
        if not r:
            return
        for p in self.active_players():
            if phase_num == 1 and not p.acted_phase1:
                p.acted_phase1 = True
                r.phase1_trades.append(TradeRecord(1, p.name, "pass", 0, None, 0, 0.0))
            elif phase_num == 2 and not p.acted_phase2:
                p.acted_phase2 = True
                r.phase2_trades.append(TradeRecord(2, p.name, "pass", 0, None, 0, 0.0))

    def _all_acted(self, phase_num: int) -> bool:
        for p in self.active_players():
            if phase_num == 1 and not p.acted_phase1:
                return False
            if phase_num == 2 and not p.acted_phase2:
                return False
        return True

    def _should_finish(self) -> bool:
        if self.round_index >= int(self.config.get("rounds", 5)):
            return True
        if len(self.active_players()) == 0:
            return True
        return False

    def _action_event(self, t: TradeRecord) -> dict:
        return {
            "type":         "player_action",
            "round_index":  self.round_index,
            "phase":        t.phase,
            "player_name":  t.player_name,
            "action":       t.action,
            "qty":          t.qty,
            "price":        t.price,
        }

    def _round_ended_event(self) -> dict:
        r = self.current_round
        return {
            "type":             "round_ended",
            "round_index":      self.round_index,
            "true_value":       r.true_value if r else None,
            "leaderboard":      self.leaderboard(),
            "player_snapshots": [
                {
                    "name":       p.name,
                    "cash":       p.cash,
                    "round_pnl":  p.round_pnl,
                    "phase1_pnl": p.phase1_pnl,
                    "phase2_pnl": p.phase2_pnl,
                    "eliminated": p.eliminated,
                }
                for p in self.players.values()
            ],
        }

    @staticmethod
    def _ev(valid_values: list[int]) -> float:
        if not valid_values:
            return 0.0
        return round(sum(valid_values) / len(valid_values), 2)

    @staticmethod
    def _trade_to_dict(t: TradeRecord) -> dict[str, Any]:
        return {
            "phase":       t.phase,
            "player_name": t.player_name,
            "action":      t.action,
            "qty":         t.qty,
            "price":       t.price,
            "signed_qty":  t.signed_qty,
            "cashflow":    t.cashflow,
        }