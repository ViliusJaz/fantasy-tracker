"""Draft-league scoring (verified against official round totals): the five court players count x1
and the captain x2, the 6th man (b-1) x1, bench b-2..b-5 x0.5 and inactive players nothing.
Court formations are centers-forwards-guards."""
import itertools


STARTER_PREFIXES = ("g", "f", "c")  # lineup card ids: g-1, f-1, c-1 start; b-* bench; i-* inactive


def slot_of(card):
    prefix = (card or "").split("-")[0]
    if prefix in STARTER_PREFIXES:
        return "starter"
    return "bench" if prefix == "b" else "inactive"


def _slot_sort_key(p):
    order = {"starter": 0, "bench": 1, "inactive": 2}
    pos_order = {"c": 0, "f": 1, "g": 2}
    return (order[p["slot"]], pos_order.get(p["card"][:1], 3) if p["slot"] == "starter" else 0, p["card"])


def players_left(lineup_players):
    """Starters whose game in the round is still to be finished."""
    return sum(
        1 for p in lineup_players
        if p["slot"] == "starter" and any(not g["completed"] and not g["canceled"] for g in p["games"])
    )


FORMATIONS = [(1, 2, 2), (2, 1, 2), (2, 2, 1), (1, 3, 1), (1, 1, 3)]

POS_INDEX = {"center": 0, "forward": 1, "guard": 2}


def slot_label(card):
    prefix, _, num = (card or "").partition("-")
    if prefix in STARTER_PREFIXES:
        return prefix.upper()
    if prefix == "b":
        return "6th" if num == "1" else f"B{num}"
    return "Out"


def multiplier(card, captain):
    prefix, _, num = (card or "").partition("-")
    if prefix in STARTER_PREFIXES:
        return 2 if captain else 1
    if prefix == "b":
        return 1 if num == "1" else 0.5
    return 0


def _fits_formation(group):
    """Can these five players cover one of the allowed formations?"""
    options = [[POS_INDEX[p] for p in pl["positions"] if p in POS_INDEX] or [0, 1, 2] for pl in group]
    for combo in itertools.product(*options):
        counts = (combo.count(0), combo.count(1), combo.count(2))
        if counts in FORMATIONS:
            return True
    return False


def optimal_score(lineup_players):
    """Best possible total from the same active players (inactive ones stay out)."""
    active = [p for p in lineup_players if p["slot"] != "inactive"]
    fp = lambda p: p.get("roundPts") or 0  # noqa: E731
    everyone = sum(fp(p) for p in active)
    best = None
    for court in itertools.combinations(active, min(5, len(active))):
        if len(court) == 5 and not _fits_formation(court):
            continue
        on_court = {id(p) for p in court}
        rest = [p for p in active if id(p) not in on_court]
        sixth = max((fp(p) for p in rest), default=0)
        court_sum = sum(fp(p) for p in court)
        captain = max((fp(p) for p in court), default=0)
        # bench counts half, so: half of everyone + the other half for court + 6th, + captain bonus
        total = everyone / 2 + (court_sum + sixth) / 2 + captain
        if best is None or total > best:
            best = total
    return round(best or 0, 2)


def score_lineup(lineup_players):
    """Adds slotLabel / mult / contrib to each player and returns the lineup totals."""
    for p in lineup_players:
        p["slotLabel"] = slot_label(p["card"])
        p["mult"] = multiplier(p["card"], p["captain"])
        p["contrib"] = round((p.get("roundPts") or 0) * p["mult"], 2)
    total = round(sum(p["contrib"] for p in lineup_players), 2)
    optimal = optimal_score(lineup_players)
    return {"total": total, "optimal": optimal, "lost": round(max(optimal - total, 0), 2)}
