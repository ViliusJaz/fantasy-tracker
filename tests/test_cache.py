"""The persistent cache: what is kept, for how long, and that live data never is."""
import time

import pytest

from conftest import HLA


@pytest.fixture
def cache(ft, monkeypatch):
    from backend import cache as module
    monkeypatch.setattr(module, "DISABLED", False)
    monkeypatch.setattr(module, "STATS", {"hits": 0, "stored": 0})
    return module


def test_put_get_and_expiry(cache, monkeypatch):
    cache.put("k", "basketnews", {"a": [1, 2]}, keep=60)
    assert cache.get("k") == {"a": [1, 2]}
    later = time.time() + 61
    monkeypatch.setattr(cache.time, "time", lambda: later)
    assert cache.get("k") is None
    assert cache.prune() == 1


def test_nothing_is_kept_without_a_keep_time(cache):
    cache.put("k", "basketnews", {"a": 1}, keep=0)
    assert cache.get("k") is None


def test_clear_by_source(cache):
    cache.put("a", "basketnews", 1, keep=60)
    cache.put("b", "advanced-stats", 2, keep=60)
    assert cache.clear("basketnews") == 1
    assert cache.get("a") is None and cache.get("b") == 2
    assert cache.clear() == 1


def test_disabled_cache_reads_and_writes_nothing(cache, monkeypatch):
    cache.put("k", "basketnews", 1, keep=60)
    monkeypatch.setattr(cache, "DISABLED", True)
    assert cache.get("k") is None
    cache.put("j", "basketnews", 2, keep=60)
    monkeypatch.setattr(cache, "DISABLED", False)
    assert cache.get("j") is None


@pytest.mark.parametrize("rnd,kept", [(0, True), (2, True), (3, False), (4, False), (5, False), (6, True), (37, True)])
def test_keep_policy(ft, rnd, kept):
    meta = {"currentRound": 4}
    assert (ft.keep_for(meta, rnd) > 0) is kept  # the previous, current and next round are live


def test_gql_uses_a_kept_answer_without_asking_again(ft, cache, monkeypatch):
    calls = []
    monkeypatch.setattr(ft, "fetch", lambda *a, **k: calls.append(1) or {"data": {"n": len(calls)}})
    assert ft.gql("query { settled }", {"r": 1}, keep=3600) == {"n": 1}
    ft._cache.clear()  # a new run: memory is empty
    assert ft.gql("query { settled }", {"r": 1}, keep=3600) == {"n": 1}
    assert len(calls) == 1 and cache.STATS["hits"] == 1
    ft._cache.clear()
    assert ft.gql("query { live }", {"r": 5}) == {"n": 2}  # without keep: always asked
    ft._cache.clear()
    assert ft.gql("query { live }", {"r": 5}) == {"n": 3}


def test_a_kept_round_list_takes_season_fields_from_today(ft, monkeypatch):
    def view(pid, avg, health, rpts):
        return {"id": pid, "avgPts": avg, "gamesPlayed": 3, "season": {"pts": avg}, "health": health,
                "roundPts": rpts, "roundLine": None, "club": None, "games": [], "bnId": None}
    old_round = {"p1": view("p1", 10.0, "ready", 25.0)}
    today = {"p1": view("p1", 14.0, "out", None)}
    meta = {"currentRound": 5, "latestRound": 5, "firstRound": 0, "bnLeagueId": None, "seasonYear": None}

    def fetch_players(meta_, stats_round, games_round, ttl, keep=0):
        return {k: dict(v) for k, v in (old_round if stats_round == 1 else today).items()}
    monkeypatch.setattr(ft, "fetch_players", fetch_players)
    p = ft.players(meta, 1, 1)["p1"]
    assert p["roundPts"] == 25.0                                   # the round's own numbers stay
    assert (p["avgPts"], p["health"], p["season"]["pts"]) == (14.0, "out", 14.0)  # season ones are today's
