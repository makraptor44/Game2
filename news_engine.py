from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class NewsRule:
    row_id: str
    tier: str
    headline: str
    scope: str          # "deck" | "round"
    kind: str
    allowed_values: list[int]   # card values still possible after this news
    affects_ev: bool
    raw: dict[str, Any]


class NewsEngine:
    """
    News engine for Market Edge.

    Key fixes vs previous version:
    - Reads 'news_text' column for headlines (was looking for wrong column names)
    - Reads 'suggested_scope' for DECK vs ROUND scope
    - Reads 'allowed_values_json' to get the constrained card value set
    - _rule_holds() now properly evaluates CSV-style headlines
    - compute_valid_totals() returns all possible sums given constraints,
      enabling the market price to move when news is released
    """

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.rules: list[NewsRule] = []
        self.skipped_rows: list[dict[str, str]] = []
        self._load_rules()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def draw_cards(self, n: int) -> list[int]:
        """Draw n random card values (2-14, with replacement for simplicity)."""
        return [random.randint(2, 14) for _ in range(n)]

    def skip_report(self) -> list[dict[str, str]]:
        return list(self.skipped_rows)

    def compute_valid_totals(
        self,
        cards: list[int],
        revealed_cards: list[int],
        active_news: list[dict[str, Any]],
    ) -> list[int]:
        """
        Return the list of all possible round totals consistent with:
          - the revealed cards (known exactly)
          - the active news constraints (restrict which hidden card values are possible)

        Each hidden card is assumed to be independently drawn from the
        intersection of allowed-value sets across all active news that
        constrains a SINGLE hidden card.

        For simplicity we enumerate combinations of hidden card values and
        sum with the revealed total — this gives a distribution of possible
        totals from which the market EV is computed.
        """
        n_hidden = len(cards) - len(revealed_cards)
        revealed_total = sum(revealed_cards)

        if n_hidden == 0:
            return [revealed_total + sum(cards[len(revealed_cards):])]

        # Build the set of values each hidden card could take
        # Start with full range 2-14
        possible_per_hidden: list[set[int]] = [set(range(2, 15)) for _ in range(n_hidden)]

        # Apply single-card constraints from active news
        for item in active_news:
            av = item.get("allowed_values")
            if av and isinstance(av, list):
                av_set = set(av)
                # Apply to ALL hidden positions (conservative: news applies to deck)
                for i in range(n_hidden):
                    possible_per_hidden[i] = possible_per_hidden[i] & av_set

        # Enumerate all combinations of hidden card values
        # Cap at reasonable size to avoid explosion
        from itertools import product
        combos = list(product(*[sorted(s) for s in possible_per_hidden]))
        if not combos:
            # Fallback: use actual cards
            return [sum(cards)]

        totals = list({revealed_total + sum(combo) for combo in combos})
        return sorted(totals)

    def pick_round_news(
        self,
        cards: list[int],
        revealed_cards: list[int],
        active_news: list[dict[str, Any]],
        count: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Pick up to `count` ROUND-scoped news items that are:
        - true for the actual deck/cards
        - not already active (by headline)
        - not duplicates
        Prefer items that affect EV (tier 1-2) for interesting gameplay.
        """
        used_headlines = {self._headline_of(n) for n in active_news}
        candidates: list[NewsRule] = []

        for rule in self.rules:
            if rule.scope != "round":
                continue
            if rule.headline in used_headlines:
                continue
            if self._rule_holds(rule, cards, revealed_cards):
                candidates.append(rule)

        # Prefer ev-affecting news, then shuffle within each group
        ev_affecting = [r for r in candidates if r.affects_ev]
        non_ev = [r for r in candidates if not r.affects_ev]
        random.shuffle(ev_affecting)
        random.shuffle(non_ev)
        ordered = ev_affecting + non_ev

        chosen = ordered[:count]
        return [self._rule_to_payload(rule) for rule in chosen]

    def pick_persistent_deck_news(
        self,
        cards: list[int],
        active_news: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """
        Pick one DECK-scoped news item that is true and not already used.
        Deck news persists across rounds and keeps narrowing the EV.
        """
        used_headlines = {self._headline_of(n) for n in active_news}
        candidates: list[NewsRule] = []

        for rule in self.rules:
            if rule.scope != "deck":
                continue
            if rule.headline in used_headlines:
                continue
            # For deck news, evaluate against ALL 5 cards
            if self._rule_holds(rule, cards, []):
                candidates.append(rule)

        if not candidates:
            return None

        # Prefer ev-affecting items
        ev_affecting = [r for r in candidates if r.affects_ev]
        pool = ev_affecting if ev_affecting else candidates
        return self._rule_to_payload(random.choice(pool))

    # ------------------------------------------------------------------ #
    # CSV loading
    # ------------------------------------------------------------------ #

    def _load_rules(self):
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):
                cleaned = {
                    str(k).strip(): (str(v).strip() if v is not None else "")
                    for k, v in row.items()
                }

                row_id = cleaned.get("news_id") or str(idx)

                # FIX 1: CSV uses 'news_text' as the headline column
                headline = cleaned.get("news_text", "").strip()
                if not headline:
                    self.skipped_rows.append({"row_id": row_id, "reason": "missing headline"})
                    continue

                lower = headline.lower()

                # Skip suit/colour rows — no suit data in current engine
                if any(t in lower for t in [
                    "suit", "colour", "color", "hearts", "diamonds", "clubs", "spades",
                    "red", "black",
                ]):
                    self.skipped_rows.append({"row_id": row_id, "reason": f"suit/colour: {headline}"})
                    continue

                # FIX 2: Read scope from 'suggested_scope' column
                raw_scope = cleaned.get("suggested_scope", "").strip().upper()
                scope = "deck" if raw_scope == "DECK" else "round"

                # FIX 3: Read allowed_values from 'allowed_values_json'
                allowed_values: list[int] = []
                av_raw = cleaned.get("allowed_values_json", "").strip()
                if av_raw:
                    try:
                        parsed = json.loads(av_raw)
                        if isinstance(parsed, list):
                            allowed_values = [int(x) for x in parsed]
                    except (json.JSONDecodeError, ValueError):
                        pass

                affects_ev_raw = cleaned.get("affects_ev", "0").strip()
                affects_ev = affects_ev_raw in ("1", "true", "True", "yes")

                tier = cleaned.get("tier_id", cleaned.get("tier", "1"))
                kind = cleaned.get("category", self._infer_kind(headline))

                self.rules.append(NewsRule(
                    row_id=str(row_id),
                    tier=str(tier),
                    headline=headline,
                    scope=scope,
                    kind=kind,
                    allowed_values=allowed_values,
                    affects_ev=affects_ev,
                    raw=cleaned,
                ))

    # ------------------------------------------------------------------ #
    # Rule evaluation — matches CSV headline patterns
    # ------------------------------------------------------------------ #

    def _rule_holds(
        self,
        rule: NewsRule,
        cards: list[int],
        revealed_cards: list[int],
    ) -> bool:
        """
        Evaluate whether a news headline is TRUE for the given cards.

        Covers the headline patterns present in the CSV:
          - "No Xs remain in the deck"
          - "Remaining cards are greater/less than N"
          - "Remaining cards lie between X and Y inclusive"
          - "No face cards / Only face cards / Only number cards remain"
          - "Only odd/even/prime/composite/square/fibonacci/triangular/powers of two/cube values remain"
          - "Only multiples of N remain" / "Only non-multiples of N remain"
          - "Only odd/even values greater/less than N remain"
          - "Only prime values above/below N remain"
          - "The last hidden card is ..."  (phase 2, 1 hidden card)
          - "The two hidden cards ..."     (phase 1 or 2, 2 hidden cards)
          - "Both hidden cards ..."
          - "At least one hidden card ..."
          - "Exactly one hidden card ..."
          - "The hidden cards sum to ..."
          - "One hidden card is at least double the other"
          - "The larger hidden card is at most 2 above the smaller"
          - Dynamic board-dependent items (BOARD_DEPENDENT) — skipped
        """
        text = rule.headline.lower()
        all_cards = list(cards)
        hidden = cards[len(revealed_cards):]
        nums = self._extract_ints(text)

        # ── BOARD_DEPENDENT: too complex to evaluate statically ──
        if not rule.allowed_values and rule.kind in ("dynamic_structural",):
            return False

        # ── Shortcut: if allowed_values is populated, just check whether
        #    all actual card values are within the allowed set ──
        if rule.allowed_values:
            av_set = set(rule.allowed_values)
            # For deck-scope: all cards must be in set
            if rule.scope == "deck":
                return all(c in av_set for c in all_cards)
            # For round-scope with hidden card constraints:
            if hidden:
                return all(c in av_set for c in hidden)
            return True

        # ── Fallback text-based evaluation for rows without allowed_values ──

        # "The two hidden cards have the same parity"
        if "same parity" in text and len(hidden) >= 2:
            return hidden[0] % 2 == hidden[1] % 2

        # "The two hidden cards have opposite parity"
        if "opposite parity" in text and len(hidden) >= 2:
            return hidden[0] % 2 != hidden[1] % 2

        # "The two hidden cards are consecutive numbers"
        if "consecutive" in text and len(hidden) >= 2:
            return abs(hidden[0] - hidden[1]) == 1

        # "The two hidden cards differ by exactly 2"
        if "differ by exactly 2" in text and len(hidden) >= 2:
            return abs(hidden[0] - hidden[1]) == 2

        # "The hidden cards sum to an even number"
        if "sum to an even" in text and hidden:
            return sum(hidden) % 2 == 0

        # "The hidden cards sum to an odd number"
        if "sum to an odd" in text and hidden:
            return sum(hidden) % 2 == 1

        # "The hidden cards sum to a prime number"
        if "sum to a prime" in text and hidden:
            return self._is_prime(sum(hidden))

        # "The hidden cards sum to a multiple of 5"
        if "sum to a multiple of 5" in text and hidden:
            return sum(hidden) % 5 == 0

        # "At least one hidden card is above 10"
        if "at least one hidden card is above" in text and nums and hidden:
            return any(c > nums[-1] for c in hidden)

        # "Both hidden cards are above 8"
        if "both hidden cards are above" in text and nums and len(hidden) >= 2:
            return all(c > nums[-1] for c in hidden)

        # "Exactly one hidden card is a face card"
        if "exactly one hidden card is a face card" in text and hidden:
            return sum(1 for c in hidden if c >= 11) == 1

        # "At least one hidden card is a square number"
        if "at least one hidden card is a square" in text and hidden:
            return any(self._is_perfect_square(c) for c in hidden)

        # "One hidden card is at least double the other"
        if "at least double the other" in text and len(hidden) >= 2:
            a, b = hidden[0], hidden[1]
            return a >= 2 * b or b >= 2 * a

        # "The larger hidden card is at most 2 above the smaller"
        if "at most 2 above the smaller" in text and len(hidden) >= 2:
            return abs(hidden[0] - hidden[1]) <= 2

        # "The hidden cards are both multiples of 3"
        if "both multiples of 3" in text and len(hidden) >= 2:
            return all(c % 3 == 0 for c in hidden)

        # "The hidden cards share the same modulo-3 class"
        if "same modulo-3 class" in text and len(hidden) >= 2:
            return hidden[0] % 3 == hidden[1] % 3

        return False

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def _rule_to_payload(self, rule: NewsRule) -> dict[str, Any]:
        return {
            "row_id": rule.row_id,
            "tier": rule.tier,
            "headline": rule.headline,
            "scope": rule.scope,
            "kind": rule.kind,
            "allowed_values": rule.allowed_values,
            "affects_ev": rule.affects_ev,
        }

    @staticmethod
    def _headline_of(item: dict[str, Any]) -> str:
        return str(item.get("headline") or item.get("text") or "").strip()

    @staticmethod
    def _extract_ints(text: str) -> list[int]:
        out: list[int] = []
        current = ""
        for ch in text:
            if ch.isdigit():
                current += ch
            else:
                if current:
                    out.append(int(current))
                    current = ""
        if current:
            out.append(int(current))
        return out

    @staticmethod
    def _infer_kind(headline: str) -> str:
        text = headline.lower()
        if "sum" in text or "total" in text:
            return "sum"
        if "odd" in text or "even" in text or "parity" in text:
            return "parity"
        if "greater than" in text or "less than" in text or "at least" in text or "at most" in text:
            return "comparison"
        if "prime" in text or "composite" in text or "square" in text or "fibonacci" in text:
            return "number_class"
        if "multiple" in text:
            return "modular"
        if "face card" in text:
            return "group_range_filter"
        return "generic"

    @staticmethod
    def _is_prime(n: int) -> bool:
        if n < 2:
            return False
        for i in range(2, int(n ** 0.5) + 1):
            if n % i == 0:
                return False
        return True

    @staticmethod
    def _is_perfect_square(n: int) -> bool:
        if n < 0:
            return False
        root = int(n ** 0.5)
        return root * root == n