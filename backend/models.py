"""The tracker's own records, as the source adapters (backend/sources) produce them.

Plain dicts keep the JSON output exactly as it was; these TypedDicts document their shape
in one place, and validation.py checks fetched data against the required keys. The rest of
the backend works with these names only, never with BasketNews field names.
"""
from typing import List, Optional, TypedDict


class TeamRef(TypedDict):
    id: str
    title: str
    owner: str                     # "Vilius J." (first name and last initial) or ""


class StandingRow(TypedDict, total=False):
    team: TeamRef
    position: int
    positionGained: int            # BasketNews' value: new place minus old (-2 = up two places)
    pointsTotal: float
    pointsRound: float
    wins: int                      # head-to-head only
    losses: int
    ties: int
    roundPosition: Optional[int]   # classic only


class Match(TypedDict):
    id: str
    team1: Optional[TeamRef]
    team2: Optional[TeamRef]
    score1: float
    score2: float


class LineupPlayer(TypedDict):
    id: str
    card: str                      # g-1 / f-2 / c-1 court, b-1..b-5 bench, i-* inactive
    slot: str                      # "starter" | "bench" | "inactive"
    captain: bool


class Lineup(TypedDict):
    round: int
    formation: Optional[str]       # centers-forwards-guards, e.g. "1-2-2"
    players: List[LineupPlayer]


class Game(TypedDict, total=False):
    at: str                        # ISO start time
    home: bool
    opponent: str                  # club abbreviation
    opponentLogo: Optional[str]
    score: Optional[List[int]]     # [ours, theirs] once started
    live: bool
    completed: bool
    canceled: bool
    delayed: bool
    day: int                       # day of the round, when a round spans several days


class StatLine(TypedDict, total=False):
    min: float
    pts: int
    reb: int
    oreb: int
    dreb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int
    eff: int
    p2m: int
    p2a: int
    p3m: int
    p3a: int
    ftm: int
    fta: int
    sec: int
    ba: int                        # blocks against
    fd: int                        # fouls drawn
    pm: int                        # plus-minus
    usg: Optional[float]


class Club(TypedDict):
    abbr: str
    name: Optional[str]
    fullName: Optional[str]
    nameEn: Optional[str]
    logo: Optional[str]


class Player(TypedDict):
    id: str                        # fantasy player id
    bnId: Optional[str]            # basketnews.com player id (injury report, advanced stats)
    name: str
    photo: Optional[str]
    health: Optional[str]
    position: Optional[str]        # "guard" | "forward" | "center"
    positions: List[str]
    number: Optional[int]
    club: Optional[Club]
    games: List[Game]              # games of the round asked for
    roundPts: Optional[float]      # fantasy points in the round asked for
    roundPlayed: bool
    roundLine: Optional[StatLine]
    avgPts: Optional[float]        # season average fantasy points
    gamesPlayed: int
    season: Optional[StatLine]     # season per-game averages


class TransferPlayer(TypedDict):
    id: str
    name: str


class TransferSide(TypedDict):
    team: Optional[dict]           # {"id", "title"}
    players: List[TransferPlayer]  # who changed hands
    listed: List[str]              # everyone named on this side
    credits: float


class Transfer(TypedDict):
    id: str
    kind: str                      # "trade" | "free_agent"
    at: Optional[str]
    offer: TransferSide            # the proposing team (a signing: players dropped and the bid)
    request: TransferSide


class Bid(TypedDict):
    playerId: str
    highestBid: Optional[float]
    totalBids: Optional[int]


class DraftPick(TypedDict):
    id: str
    teamId: str
    playerId: Optional[str]


class InjuryEntry(TypedDict):
    bnId: Optional[str]
    name: str
    club: Optional[str]
    pos: str
    status: str                    # ready | expected | questionable | game-time | doubtful | out | uncertain | other
    siteLabel: str
    return_: str                   # stored under the key "return"
    comment: str


def required(model):
    """Keys a record of this model must have (for validation)."""
    keys = set(model.__required_keys__)
    return {"return" if k == "return_" else k for k in keys}
