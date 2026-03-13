# Game2 🃏
 
A data-driven card game analysis and strategy engine that calculates optimal decision-making using market edge theory, news-based modifiers, and heuristic scoring.
 
---
 
## What It Does
 
Game2 analyses card game states in real time to recommend optimal plays. It evaluates the current deck composition, applies news-tier modifiers, and scores each possible action using a multi-factor heuristic model.
 
Key capabilities:
- **Market Edge Calculation** — scores every possible game action by expected value shift
- **News Engine** — applies contextual news tiers that modify card values dynamically
- **Deck State Tracking** — monitors remaining cards and updates probabilities live
- **Strategy Heuristics** — categorises moves by type (single card removal, range bounds, parity, number class, etc.)
- **Web Interface** — interactive UI to play, analyse, and visualise game states
 
---
 
## Project Structure
 
```
Game2/
├── app.js                              # Frontend entry point
├── style.css                           # UI styling
├── index.html                          # Main web interface
├── server.py                           # Backend server
├── dbmain.py                           # Database management
├── game_state.py                       # Core game state logic
├── news_engine.py                      # News tier modifier engine
├── requirements.txt                    # Python dependencies
└── market_edge_news_tiers_rebuilt.csv  # Strategy/heuristic dataset
```
 
---
 
## The Dataset
 
The core of the engine is `market_edge_news_tiers_rebuilt.csv`, which contains hundreds of pre-computed game scenarios with:
 
| Column | Description |
|---|---|
| `news_text` | Human-readable description of the game state |
| `suggested_scope` | What the rule applies to (DECK, HAND, etc.) |
| `tier_id` | News tier classification |
| `calc_steps_heuristic` | Step-by-step calculation logic |
| `category` | Move type (single_value_removal, parity, number_class, etc.) |
| `new_average_value_base` | Expected average card value after action |
| `ev_shift_per_hid` | Expected value shift per hidden card |
 
---
