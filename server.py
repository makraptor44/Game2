from dotenv import load_dotenv
load_dotenv()

import os
import threading
import time
import uuid
from queue import Empty, Queue

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit, join_room

import dbmain
from game_state import Command, CommandType, GameState, Phase
from news_engine import NewsEngine

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Look for the CSV next to server.py first, then fall back to /mnt/data/
_CSV_LOCAL = os.path.join(BASE_DIR, "market_edge_news_tiers_rebuilt.csv")
_CSV_MOUNT = "/mnt/data/market_edge_news_tiers_rebuilt.csv"
NEWS_CSV = _CSV_LOCAL if os.path.exists(_CSV_LOCAL) else _CSV_MOUNT

NEWS_ENGINE = NewsEngine(NEWS_CSV)

COMMANDS: Queue[Command] = Queue()
GAMES: dict[str, GameState] = {}

TICK            = 0.05   # server loop interval (seconds)
MAX_NAME_LENGTH = 20
MIN_NAME_LENGTH = 2

# ── Flask / SocketIO ───────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "market-edge-dev")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
dbmain.init_db()


# ── HTTP routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "ok":             True,
        "games":          len(GAMES),
        "supported_news": len(NEWS_ENGINE.rules),
        "skipped_news":   len(NEWS_ENGINE.skipped_rows),
    })


@app.route("/news-diagnostics")
def news_diagnostics():
    return jsonify({
        "supported": len(NEWS_ENGINE.rules),
        "skipped":   NEWS_ENGINE.skip_report(),
    })


@app.route("/spec")
def spec():
    return jsonify({
        "starting_bankroll": 1000,
        "phase_unit_limit":  20,
        "spread":            2,
        "rounds":            5,
        "min_players":       2,
        "max_players":       100,
    })


# ── Broadcast ──────────────────────────────────────────────────────────────────

def broadcast_state(game: GameState):
    """Send each player their personalised state snapshot."""
    for name, player in game.players.items():
        if not player.connected or not player.sid:
            continue
        payload = game.player_state(name)
        socketio.emit("state_update", payload, to=player.sid)


# ── Socket events ──────────────────────────────────────────────────────────────

@socketio.on("CreateGame")
def on_create_game(data):
    config = {
        "rounds":          int(data.get("rounds", 5)),
        "card_no":         5,
        "phase1_seconds":  int(data.get("phase1_seconds", 30)),
        "phase2_seconds":  int(data.get("phase2_seconds", 30)),
        "reveal_seconds":  int(data.get("reveal_seconds", 8)),
        "results_seconds": int(data.get("results_seconds", 10)),
        "spread":          float(data.get("spread", 2)),
        "phase_unit_limit": int(data.get("phase_unit_limit", 20)),
        "min_players":     max(2, int(data.get("min_players", 2))),
        "max_players":     min(100, max(2, int(data.get("max_players", 100)))),
    }
    game_id = uuid.uuid4().hex[:8]
    GAMES[game_id] = GameState(game_id=game_id, config=config, news_engine=NEWS_ENGINE)
    dbmain.create_game(game_id, config)
    emit("game_created", {"game_id": game_id, "config": config})


@socketio.on("JoinGame")
def on_join_game(data):
    game_id = (data.get("game_id") or "").strip().lower()
    name    = (data.get("name") or "").strip()

    if not game_id or game_id not in GAMES:
        emit("error", {"message": "Invalid game code"})
        return
    if not name:
        emit("error", {"message": "Name cannot be empty"})
        return
    if len(name) < MIN_NAME_LENGTH or len(name) > MAX_NAME_LENGTH:
        emit("error", {"message": f"Name must be {MIN_NAME_LENGTH}–{MAX_NAME_LENGTH} characters"})
        return

    game = GAMES[game_id]
    if game.phase != Phase.LOBBY:
        emit("error", {"message": "Game has already started"})
        return
    if name in game.players:
        emit("error", {"message": "That name is already taken"})
        return

    role = "host" if len(game.players) == 0 else "player"
    COMMANDS.put(Command(
        type=CommandType.JOIN,
        game_id=game_id,
        sid=_sid(),
        payload={"name": name, "role": role},
    ))
    join_room(game_id)


@socketio.on("StartGame")
def on_start_game(data):
    game_id = (data.get("game_id") or "").strip().lower()
    if not game_id or game_id not in GAMES:
        emit("error", {"message": "Invalid game code"})
        return
    COMMANDS.put(Command(
        type=CommandType.START_GAME,
        game_id=game_id,
        sid=_sid(),
        payload={},
    ))


@socketio.on("PlayerAction")
def on_player_action(data):
    game_id = (data.get("game_id") or "").strip().lower()
    if not game_id or game_id not in GAMES:
        emit("error", {"message": "Invalid game code"})
        return
    COMMANDS.put(Command(
        type=CommandType.PLAYER_ACTION,
        game_id=game_id,
        sid=_sid(),
        payload={
            "action": data.get("action"),
            "qty":    int(data.get("qty") or 0),
        },
    ))


@socketio.on("disconnect")
def on_disconnect():
    sid = _sid()
    for game in GAMES.values():
        name = game.sid_to_name.get(sid)
        if name and name in game.players:
            game.players[name].connected = False
            break


def _sid() -> str:
    return request.sid


# ── DB event handler ───────────────────────────────────────────────────────────

def handle_events_db(game: GameState, events: list[dict], game_id: str):
    for event in events:
        et = event.get("type")
        if et == "player_joined":
            dbmain.upsert_player(game_id, event["player"]["name"], event["player"]["role"])
        elif et == "round_started":
            dbmain.create_round(game_id, event["round_index"])
        elif et == "player_action":
            dbmain.insert_ledger_entry(
                game_id,
                event.get("round_index", game.round_index),
                event["player_name"],
                event["action"],
                event["qty"],
                event.get("price"),
            )
        elif et == "round_ended":
            dbmain.save_leaderboard(game_id, event.get("leaderboard", []))
        elif et == "game_finished":
            dbmain.finish_game(game_id)


# ── Game loop ──────────────────────────────────────────────────────────────────

def game_loop():
    while True:
        start = time.time()

        # Drain command queue
        while True:
            try:
                cmd = COMMANDS.get_nowait()
            except Empty:
                break
            game = GAMES.get(cmd.game_id)
            if not game:
                continue
            changed, events = game.apply_command(cmd)
            handle_events_db(game, events, cmd.game_id)
            if changed:
                broadcast_state(game)

        # Tick all games for timer-driven transitions
        for game in list(GAMES.values()):
            changed, events = game.tick()
            handle_events_db(game, events, game.game_id)
            if changed:
                broadcast_state(game)

        elapsed = time.time() - start
        time.sleep(max(0, TICK - elapsed))


threading.Thread(target=game_loop, daemon=True).start()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Market Edge on {host}:{port}")
    print(f"News rules loaded: {len(NEWS_ENGINE.rules)}")
    print(f"News rows skipped: {len(NEWS_ENGINE.skipped_rows)}")
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)