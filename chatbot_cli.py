"""
Tinder-style real-estate chatbot — CLI
───────────────────────────────────────
Flow
  1. Chatbot asks the user a series of questions (budget, type, location, …)
  2. Recommender returns ranked listings that match.
  3. User swipes → (L)ike  (D)islike  (S)kip  (Q)uit
  4. After each batch the weights are updated from the swipe history.
  5. A liked-listings summary is shown at the end.

Run
  source .venv/bin/activate
  python chatbot_cli.py
"""

import os
import sys
import textwrap
from dataclasses import asdict

# Make sure the script works when run from any directory
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from recommender import Recommender, UserPreferences, AMENITY_COLS

# ─────────────────────────────────────────────────────────────────────────────
# Terminal colours (graceful fallback if not supported)
# ─────────────────────────────────────────────────────────────────────────────

def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_COLOR = _supports_color()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

def bold(t):    return _c(t, "1")
def green(t):   return _c(t, "32")
def red(t):     return _c(t, "31")
def yellow(t):  return _c(t, "33")
def cyan(t):    return _c(t, "36")
def dim(t):     return _c(t, "2")


# ─────────────────────────────────────────────────────────────────────────────
# Input helpers
# ─────────────────────────────────────────────────────────────────────────────

def ask(prompt: str, default=None) -> str:
    hint = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{cyan('?')} {bold(prompt)}{dim(hint)}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return raw if raw else (str(default) if default is not None else "")


def ask_int(prompt: str, default=None, min_val=None, max_val=None) -> int | None:
    while True:
        raw = ask(prompt, default)
        if not raw:
            return None
        try:
            val = int(raw.replace(" ", "").replace(",", ""))
            if min_val is not None and val < min_val:
                print(f"  {red('✗')} Minimum value: {min_val}")
                continue
            if max_val is not None and val > max_val:
                print(f"  {red('✗')} Maximum value: {max_val}")
                continue
            return val
        except ValueError:
            print(f"  {red('✗')} Please enter a number.")


def ask_choice(prompt: str, choices: list[str], default=None) -> str:
    """Numbered menu selection."""
    print(f"{cyan('?')} {bold(prompt)}")
    for i, c in enumerate(choices, 1):
        marker = dim(f"  {i}.")
        print(f"{marker} {c}")
    while True:
        raw = ask("Your choice (number or text)", default)
        if not raw:
            return default or ""
        # Accept number
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        # Accept text match (case-insensitive prefix)
        matches = [c for c in choices if c.lower().startswith(raw.lower())]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f"  {yellow('!')} Ambiguous — did you mean: {', '.join(matches)}?")
            continue
        print(f"  {red('✗')} Not recognised. Choose a number from the list.")


def ask_multi_choice(prompt: str, choices: list[str]) -> list[str]:
    """Comma-separated multi-select from a numbered list."""
    print(f"{cyan('?')} {bold(prompt)} {dim('(comma-separated numbers, or Enter to skip)')}")
    for i, c in enumerate(choices, 1):
        print(f"  {dim(str(i)+'.')} {c}")
    raw = ask("Your selection", "")
    if not raw:
        return []
    selected = []
    for token in raw.replace(" ", "").split(","):
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(choices):
                selected.append(choices[idx])
    return selected


def ask_yes_no(prompt: str, default: bool = None) -> bool | None:
    hint = " (o/n)" if default is None else (" [O/n]" if default else " [o/N]")
    raw = ask(prompt + hint, "").lower()
    if not raw:
        return default
    return raw in ("o", "oui", "y", "yes", "1")


# ─────────────────────────────────────────────────────────────────────────────
# Card display
# ─────────────────────────────────────────────────────────────────────────────

AMENITY_LABELS = {
    "has_garage":            "Garage",
    "has_terrasse":          "Terrasse",
    "has_jardin":            "Jardin",
    "has_ascenseur":         "Ascenseur",
    "has_cuisine_equipee":   "Cuisine équipée",
    "has_concierge":         "Concierge",
    "has_climatisation":     "Climatisation",
    "has_securite":          "Sécurité",
    "has_salon_europeen":    "Salon européen",
    "has_salon_marocain":    "Salon marocain",
    "has_piscine":           "Piscine",
    "has_meuble":            "Meublé",
    "has_vue_mer":           "Vue sur mer",
    "has_cheminee":          "Cheminée",
    "has_chauffage_central": "Chauffage central",
    "has_double_vitrage":    "Double vitrage",
    "has_porte_blindee":     "Porte blindée",
    "has_machine_laver":     "Machine à laver",
}

def _fmt_price(p) -> str:
    try:
        return f"{int(p):,} MAD".replace(",", " ")
    except (ValueError, TypeError):
        return "Prix N/A"


def display_card(listing: dict, rank: int, total: int) -> None:
    """Print a single listing card."""
    w = 60
    sep = "─" * w

    price_str  = _fmt_price(listing.get("price"))
    surface    = listing.get("surface")
    surface_str = f"{surface} m²" if surface else "N/A"
    bedrooms   = listing.get("bedrooms")
    bathrooms  = listing.get("bathrooms")
    rooms      = listing.get("rooms")
    nbhd       = listing.get("neighborhood", "N/A")
    ptype      = listing.get("property_type", "N/A")
    state      = listing.get("state", "N/A")
    standing   = listing.get("standing", "")
    score      = listing.get("score", 0)
    tags       = listing.get("match_tags", [])
    url        = listing.get("url", "")

    # Present amenities that the listing actually has
    amenities = [label for col, label in AMENITY_LABELS.items()
                 if listing.get(col) == 1]

    print(f"\n{sep}")
    print(f"  {bold(f'Annonce {rank}/{total}')}  {dim(f'Score: {score:.0%}')}")
    print(sep)
    print(f"  {bold(price_str)}  ·  {ptype}  ·  {nbhd}")
    print(f"  {surface_str}  |  "
          f"{f'{int(bedrooms)} ch.' if bedrooms else ''}  "
          f"{f'{int(bathrooms)} sdb.' if bathrooms else ''}  "
          f"{f'{int(rooms)} pièces' if rooms else ''}")
    if standing and standing != "Non précisé":
        print(f"  {yellow('⭐')} {standing}  ·  {state}")
    else:
        print(f"  {state}")
    if amenities:
        line = "  " + "  ·  ".join(amenities[:6])
        if len(amenities) > 6:
            line += f"  +{len(amenities)-6} autres"
        print(line)
    if tags:
        print(f"  {green('✓')} " + "   ".join(tags))
    # Wrap URL
    short_url = textwrap.shorten(url, width=w - 4, placeholder="…") if len(url) > w - 4 else url
    print(f"  {dim(short_url)}")
    print(sep)


def swipe_prompt() -> str:
    """Return 'like', 'dislike', 'skip', or 'quit'."""
    print(f"  {green('[O]')} J'aime   "
          f"{red('[N]')} Pas pour moi   "
          f"{yellow('[S]')} Passer   "
          f"{dim('[Q]')} Quitter")
    while True:
        try:
            key = input("  Votre choix: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"
        if key in ("o", "oui", "y", "yes", "l", "like"):
            return "like"
        if key in ("n", "non", "d", "dislike"):
            return "dislike"
        if key in ("s", "skip", "p", "passer"):
            return "skip"
        if key in ("q", "quit", "quitter"):
            return "quit"
        print(f"  {red('✗')} Tapez O / N / S / Q")


# ─────────────────────────────────────────────────────────────────────────────
# Chatbot conversation
# ─────────────────────────────────────────────────────────────────────────────

def collect_preferences(rec: Recommender) -> UserPreferences:
    """Ask the user a series of questions and return a UserPreferences object."""
    print()
    print(bold("=" * 60))
    print(bold("  🏠  Bienvenue — Trouvons votre bien idéal !"))
    print(bold("=" * 60))
    print(dim("  Répondez aux questions suivantes. Appuyez sur Entrée pour passer.\n"))

    # ── 1. Transaction type ──────────────────────────────────────────────────
    tx = ask_choice(
        "Vous cherchez à :",
        ["Acheter (Vente)", "Louer (Location)"],
        default="Acheter (Vente)",
    )
    transaction_type = "Vente" if "Vente" in tx or "Achet" in tx else "Location"

    # ── 2. Property type ─────────────────────────────────────────────────────
    type_choices = ["Appartement", "Villa", "Studio", "Duplex",
                    "Penthouse", "Maison", "Pas de préférence"]
    raw_type = ask_choice("Type de bien :", type_choices, default="Pas de préférence")
    property_type = None if "préférence" in raw_type else raw_type

    # ── 3. Budget ─────────────────────────────────────────────────────────────
    print()
    if transaction_type == "Vente":
        print(dim("  Budget en MAD  (ex: 1500000)"))
        price_min = ask_int("Budget minimum", default=None, min_val=0)
        price_max = ask_int("Budget maximum", default=None, min_val=0)
    else:
        print(dim("  Loyer mensuel en MAD  (ex: 8000)"))
        price_min = ask_int("Loyer minimum", default=None, min_val=0)
        price_max = ask_int("Loyer maximum", default=None, min_val=0)

    # Show market range to help the user calibrate
    prefs_probe = UserPreferences(
        transaction_type=transaction_type,
        city="Casablanca",
        property_type=property_type,
    )
    mkt = rec.price_range(prefs_probe)
    if mkt:
        print(dim(f"  ℹ️  Marché actuel — min: {_fmt_price(mkt['min'])}  "
                  f"médiane: {_fmt_price(mkt['median'])}  "
                  f"max: {_fmt_price(mkt['max'])}  "
                  f"({mkt['count']} annonces)"))

    # ── 4. Surface ───────────────────────────────────────────────────────────
    print()
    surface_min = ask_int("Surface minimum (m²)", default=None, min_val=0)
    surface_max = ask_int("Surface maximum (m²)", default=None, min_val=0)

    # ── 5. Rooms ─────────────────────────────────────────────────────────────
    print()
    bedrooms = ask_int("Nombre de chambres souhaité", default=None, min_val=0, max_val=20)

    # ── 6. Neighbourhood ─────────────────────────────────────────────────────
    print()
    all_nbhds = rec.available_neighborhoods("Casablanca")
    selected_nbhds = ask_multi_choice(
        "Quartiers préférés (laissez vide = tous)",
        all_nbhds[:20],   # show top 20 by listing count
    )
    neighborhoods = selected_nbhds if selected_nbhds else None

    # ── 7. State preference ───────────────────────────────────────────────────
    print()
    state_choices = ["Neuf", "Prêt", "Habitable", "À rénover",
                     "En construction", "Pas de préférence"]
    raw_state = ask_choice("État du bien :", state_choices, default="Pas de préférence")
    state = None if "préférence" in raw_state else raw_state

    # ── 8. Standing ───────────────────────────────────────────────────────────
    print()
    standing_choices = ["Haut standing", "Moyen standing", "Pas de préférence"]
    raw_standing = ask_choice("Standing :", standing_choices, default="Pas de préférence")
    standing = None if "préférence" in raw_standing else raw_standing

    # ── 9. Key amenities ─────────────────────────────────────────────────────
    print()
    print(bold("Équipements importants pour vous ?"))
    amenity_map = {
        "Piscine":           "has_piscine",
        "Garage":            "has_garage",
        "Terrasse":          "has_terrasse",
        "Jardin":            "has_jardin",
        "Ascenseur":         "has_ascenseur",
        "Climatisation":     "has_climatisation",
        "Meublé":            "has_meuble",
        "Vue sur mer":       "has_vue_mer",
        "Cuisine équipée":   "has_cuisine_equipee",
        "Sécurité":          "has_securite",
        "Concierge":         "has_concierge",
        "Chauffage central": "has_chauffage_central",
    }
    wanted_amenities = ask_multi_choice(
        "Sélectionnez les équipements désirés",
        list(amenity_map.keys()),
    )
    amenity_kwargs = {col: True for label, col in amenity_map.items()
                     if label in wanted_amenities}

    return UserPreferences(
        transaction_type=transaction_type,
        city="Casablanca",
        price_min=price_min,
        price_max=price_max,
        property_type=property_type,
        surface_min=float(surface_min) if surface_min else None,
        surface_max=float(surface_max) if surface_max else None,
        bedrooms=bedrooms,
        neighborhoods=neighborhoods,
        state=state,
        standing=standing,
        **amenity_kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Swipe session
# ─────────────────────────────────────────────────────────────────────────────

def run_swipe_session(
    rec: Recommender,
    prefs: UserPreferences,
    batch_size: int = 10,
) -> list[dict]:
    """
    Present listings one by one.
    After each batch, update weights and fetch a fresh ranked batch.
    Returns all liked listings.
    """
    liked:    list[dict] = []
    disliked: list[dict] = []
    shown_urls: set[str] = set()
    total_seen = 0

    while True:
        # Fetch next batch (exclude already shown)
        all_results = rec.recommend(prefs, top_n=50)
        batch = [r for r in all_results if r["url"] not in shown_urls][:batch_size]

        if not batch:
            print(f"\n{yellow('!')} Plus d'annonces disponibles avec ces critères.")
            break

        for i, listing in enumerate(batch, 1):
            shown_urls.add(listing["url"])
            total_seen += 1
            display_card(listing, total_seen, len(all_results))

            action = swipe_prompt()

            if action == "like":
                liked.append(listing)
                print(f"  {green('❤')}  Ajouté à vos favoris !")
            elif action == "dislike":
                disliked.append(listing)
                print(f"  {red('✗')}  Annonce ignorée.")
            elif action == "skip":
                print(f"  {yellow('→')}  Passé.")
            elif action == "quit":
                print(f"\n{dim('Session terminée.')}")
                return liked

        # Update weights from swipes accumulated so far
        if liked or disliked:
            rec.update_from_swipes(
                liked_urls=[r["url"] for r in liked],
                disliked_urls=[r["url"] for r in disliked],
            )

        # Ask if user wants more results
        print()
        more = ask_yes_no("Voir plus d'annonces ?", default=True)
        if not more:
            break

    return liked


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def show_summary(liked: list[dict]) -> None:
    if not liked:
        print(f"\n{yellow('!')} Vous n'avez aimé aucune annonce cette session.")
        return

    print(f"\n{bold('=' * 60)}")
    print(bold(f"  ❤  Vos {len(liked)} annonce(s) favorites"))
    print(bold("=" * 60))
    for i, r in enumerate(liked, 1):
        price_str = _fmt_price(r.get("price"))
        surface   = r.get("surface")
        surface_str = f"{surface} m²" if surface else "N/A"
        bedrooms  = r.get("bedrooms")
        print(f"\n  {bold(str(i) + '.')} {price_str}  ·  {r.get('property_type')}  "
              f"·  {r.get('neighborhood')}")
        print(f"     {surface_str}"
              + (f"  ·  {int(bedrooms)} chambres" if bedrooms else ""))
        print(f"     {dim(r.get('url', ''))}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.chdir(_HERE)   # ensure relative paths resolve to the script directory

    print(bold("\n🏠  Immobilier Casablanca — Assistant de Recherche"))
    print(dim("  Ctrl+C à tout moment pour quitter\n"))

    from recommender_db import RecommenderDB
    rec = RecommenderDB()

    # ── Phase 1: collect preferences ────────────────────────────────────────
    prefs = collect_preferences(rec)

    # ── Quick sanity check ───────────────────────────────────────────────────
    candidates = rec._apply_hard_filters(prefs)
    print(f"\n{green('✓')} {bold(str(len(candidates)))} annonces correspondent à vos critères.")

    if candidates.empty:
        print(red("  Aucune annonce trouvée. Essayez d'élargir vos critères."))
        sys.exit(0)

    input(dim("\n  Appuyez sur Entrée pour commencer …"))

    # ── Phase 2: swipe session ───────────────────────────────────────────────
    liked = run_swipe_session(rec, prefs, batch_size=10)

    # ── Phase 3: summary ─────────────────────────────────────────────────────
    show_summary(liked)


if __name__ == "__main__":
    main()
