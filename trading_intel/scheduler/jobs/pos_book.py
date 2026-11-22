"""pos_book - created 2026-11-22. OI-anchored SPX customer position book + named repeat traders.

For each session T whose next-session 08:35 book exists: size = OI change (book of next session - book of T),
side = tape of T (single legs, quote-weighted) + pos_packages legs, IV fallback; position re-anchored to OI
(trading_intel/positions/book.py). Writes pos_book_daily (one row per contract), then finds the session's big
events (packages, single-leg builds, big 0DTE prints), names them with trading_intel/positions/players.py and
writes flow_player_sightings + flow_players. Sends one plain Telegram card for the newest session: the lines
dealers are shortest gamma on, their share, who holds them, and how sure we are.

Runs in the MORNING (the OI made by yesterday's session lands in the 08:35 book): chained after index_walls_am in
its existing 08:xx DSM task - no new task. Upsert on natural keys, re-runnable.
    python -m trading_intel.scheduler.jobs.pos_book [--days 3] [--backfill] [--no-telegram]
    python -m trading_intel.scheduler.jobs.pos_book --name auto-P-sing-b-d60-o-7-wna "Name I give it"
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import date, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.positions import book as B
from trading_intel.positions import book_data as D
from trading_intel.positions import book_events as E
from trading_intel.positions import players as PL
from trading_intel.positions.book_card import card
from trading_intel.timeutils import eastern_now, eastern_today

log = structlog.get_logger(__name__)
FIRST_BOOK = date(2026, 8, 1)


def _upsert(session: Session, model, rows: list[dict], keys: list[str], chunk: int = 2000) -> int:
    if not rows:
        return 0
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    upd = [c for c in rows[0] if c not in keys]
    for i in range(0, len(rows), chunk):
        stmt = insert(model).values(rows[i : i + chunk])
        stmt = stmt.on_conflict_do_update(
            index_elements=keys, set_={c: getattr(stmt.excluded, c) for c in upd}
        )
        session.execute(stmt)
    session.commit()
    return len(rows)


def compute_session(
    t: date,
    book_t: dict,
    book_n: dict,
    tape: dict,
    pkgs: list[dict],
    state: dict,
    spot: float | None,
    override: dict | None = None,
) -> list[dict]:
    """Rows of pos_book_daily for session t (pure: no DB). ``state`` = {contract: last stored row dict};
    ``override`` = {contract: lean} from a named player's known side (weight 1)."""
    override = override or {}
    atm_t, atm_n = D.atm_iv(book_t, spot), D.atm_iv(book_n, spot)
    legs = D.package_legs(pkgs)
    rows = []
    for key in set(book_n) | set(state):
        root, exp, k, cp = key
        if exp <= t:
            continue
        nxt = book_n.get(key)
        prev = state.get(key)
        if nxt is None:  # strike left the pulled window: carry unchanged
            if prev is None:
                continue
            oi_after, gamma = float(prev["oi"] or 0), prev.get("gamma")
        else:
            oi_after, gamma = nxt["oi"], nxt["gamma"]
        cur = book_t.get(key)
        oi_before = cur["oi"] if cur else (float(prev["oi"]) if prev else None)
        line = (
            None
            if prev is None
            else B.Line(
                pos=float(prev["cust_pos"] or 0),
                known=float(prev["known"] or 0),
                oi=float(prev["oi"] or 0),
                seed_oi=float(prev["seed_oi"] or 0),
            )
        )
        tp = tape.get(key, {})
        pk = legs.get(key, [0.0, 0.0])
        buy, sell = tp.get("buy", 0.0) + pk[0], tp.get("sell", 0.0) + pk[1]
        d_iv = (
            None
            if not (cur and nxt and cur.get("iv") is not None and nxt.get("iv") is not None)
            else nxt["iv"] - cur["iv"]
        )
        d_atm = (
            None
            if (root, exp) not in atm_t or (root, exp) not in atm_n
            else atm_n[(root, exp)] - atm_t[(root, exp)]
        )
        ivl = B.iv_lean(d_iv, d_atm)
        d_oi = oi_after - (oi_before if oi_before is not None else 0.0)
        lean, w = B.session_lean(buy, sell, d_oi, ivl) if (nxt is not None) else (0.0, 0.0)
        if key in override and nxt is not None:
            lean, w = override[key], 1.0
        new = B.step(line, oi_before, oi_after, lean, w)
        if new.oi <= 0 and new.pos == 0:
            continue
        rows.append(
            {
                "key": key,
                "date": t,
                "symbol": "SPX",
                "root": root,
                "expiry": exp,
                "strike": k,
                "cp": cp,
                "oi": new.oi,
                "d_oi": d_oi,
                "tape_buy": round(tp.get("buy", 0.0), 1),
                "tape_sell": round(tp.get("sell", 0.0), 1),
                "pkg_buy": pk[0],
                "pkg_sell": pk[1],
                "iv_lean": ivl,
                "lean": round(lean, 4),
                "cust_pos": round(new.pos, 1),
                "known": round(new.known, 1),
                "seed_oi": round(new.seed_oi, 1),
                "confidence": B.confidence(new),
                "gamma": gamma,
                "spot": spot,
                "mm_gamma_usd_1pct": B.gamma_usd_1pct(-new.pos, gamma, spot),
                "naive_mm_gamma_usd_1pct": B.gamma_usd_1pct(
                    new.oi if cp == "C" else -new.oi, gamma, spot
                ),
                "player_slug": prev.get("player_slug") if prev else None,
            }
        )
    return rows


def find_players(
    t: date, rows: list[dict], tape: dict, pkgs: list[dict], prints: list[dict], spot: float | None
) -> list[dict]:
    """Sightings for session t; also tags ``rows`` (player_slug) for every leg a player touched."""
    legs = D.package_legs(pkgs)
    by_key = {r["key"]: r for r in rows}
    lines = E.pair_lines(
        E.line_events(t, rows, tape, spot, legs), {k: r["lean"] for k, r in by_key.items()}
    )
    evs = E.package_events(t, pkgs, spot) + lines + E.print_events(t, prints, spot)
    out, now = [], eastern_now()
    for ev in evs:
        slug = PL.classify(ev)
        if not slug:
            continue
        ev["_slug"] = slug
        for leg in ev["legs"]:
            if leg in by_key:
                by_key[leg]["player_slug"] = slug
        out.append(
            {
                "date": t,
                "player_slug": slug,
                "ref": ev["ref"][:120],
                "structure": ev["structure"],
                "cp": ev["cp"],
                "expiry": ev["expiry"],
                "strike": ev["strike"],
                "strike2": ev["strike2"],
                "dte": ev["dte"],
                "otm_pct": ev["otm"],
                "lots": ev["lots"],
                "side": ev["side"],
                "action": ev["action"],
                "premium_usd": ev["premium"],
                "clips": ev["clips"],
                "fingerprint": PL.fingerprint(ev),
                "ingested_at": now,
                "_override": _override(slug, ev),
            }
        )
    return out


def _override(slug: str, ev: dict) -> dict:
    rule = PL.RULE_BY_SLUG.get(slug)
    return {} if rule is None or ev.get("side") == rule.side else PL.leg_leans(slug, ev)


def session_book(
    t: date,
    book_t: dict,
    book_n: dict,
    tape: dict,
    pkgs: list[dict],
    prints: list[dict],
    state: dict,
    spot: float | None,
) -> tuple[list[dict], list[dict]]:
    """compute_session + find_players; when a named spread trader's known side disagrees with what the legs showed,
    recompute those legs with the trader's side (one extra pass) and re-tag."""
    rows = compute_session(t, book_t, book_n, tape, pkgs, state, spot)
    sight = find_players(t, rows, tape, pkgs, prints, spot)
    override = {k: v for s in sight for k, v in s.get("_override", {}).items()}
    if override:
        rows = compute_session(t, book_t, book_n, tape, pkgs, state, spot, override)
        sight = find_players(t, rows, tape, pkgs, prints, spot)
        for s_ in sight:  # the corrected side is the trader's
            if PL.leg_leans(s_["player_slug"], {"structure": s_["structure"], "legs": [1, 2]}):
                s_["side"] = PL.RULE_BY_SLUG[s_["player_slug"]].side
    for s_ in sight:
        s_.pop("_override", None)
    return rows, sight


def _d(v) -> date | None:
    return v if (v is None or isinstance(v, date)) else date.fromisoformat(str(v)[:10])


def refresh_players(session: Session) -> None:
    """flow_players = seeds + every slug ever sighted; counts / first / last seen from the sightings table.
    Names Mithil gave are never overwritten."""
    from trading_intel.memory.models_positions import FlowPlayer

    have = {
        r[0]: (r[1], r[2])
        for r in session.execute(text("SELECT slug, name, status FROM flow_players"))
    }
    stats = {
        r[0]: r[1:]
        for r in session.execute(
            text(
                "SELECT player_slug, count(*), min(date), max(date) FROM flow_player_sightings GROUP BY player_slug"
            )
        )
    }
    now, rows = eastern_now(), []
    for slug in set(PL.SEED_PLAYERS) | set(stats):
        n, first, last = stats.get(slug, (0, None, None))
        first, last = _d(first), _d(last)
        name, desc = PL.SEED_PLAYERS.get(slug, (None, None))
        old_name, old_status = have.get(slug, (None, None))
        named = bool(old_name) and (slug in PL.SEED_PLAYERS or old_status == "named")
        rows.append(
            {
                "slug": slug,
                "name": old_name or name,
                "description": desc,
                "status": PL.player_status(slug, int(n), named),
                "first_seen": first,
                "last_seen": last,
                "n_sightings": int(n),
                "ingested_at": now,
            }
        )
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for r in rows:
        stmt = insert(FlowPlayer).values(r)
        upd = {
            c: getattr(stmt.excluded, c)
            for c in ("status", "first_seen", "last_seen", "n_sightings", "ingested_at")
        }
        if r["description"]:
            upd["description"] = stmt.excluded.description
        if r["name"] and not have.get(r["slug"], (None,))[0]:
            upd["name"] = stmt.excluded.name
        session.execute(stmt.on_conflict_do_update(index_elements=["slug"], set_=upd))
    session.commit()


def name_player(session: Session, slug: str, name: str) -> None:
    session.execute(
        text("UPDATE flow_players SET name = :n, status = 'named' WHERE slug = :s"),
        {"n": name, "s": slug},
    )
    session.commit()


def run(session: Session, days: int = 3, backfill: bool = False, telegram: bool = True) -> dict:
    from trading_intel.memory.models_positions import FlowPlayerSighting, PosBookDaily

    dates = D.book_dates(
        session, FIRST_BOOK if backfill else eastern_today() - timedelta(days=days * 2 + 10)
    )
    pairs = list(itertools.pairwise(dates))  # (session T, next book date)
    if not backfill:
        pairs = pairs[-days:]
    out, state, last_rows, last_sight, last_t = {}, None, [], [], None
    for t, n in pairs:
        if state is None or not backfill:
            _, state = D.load_state(session, t)
        spot = D.spot_on(session, n) or D.spot_on(session, t)
        book_t, book_n = D.load_book(session, t), D.load_book(session, n)
        tape, pkgs = D.load_tape(session, t), D.load_packages(session, t)
        rows, sight = session_book(
            t, book_t, book_n, tape, pkgs, D.big_0dte_prints(session, t), state, spot
        )
        now = eastern_now()
        db_rows = [{k: v for k, v in r.items() if k != "key"} | {"ingested_at": now} for r in rows]
        _upsert(
            session, PosBookDaily, db_rows, ["date", "symbol", "root", "expiry", "strike", "cp"]
        )
        _upsert(session, FlowPlayerSighting, sight, ["date", "player_slug", "ref"])
        state = {r["key"]: r for r in rows}
        out[str(t)] = {
            "lines": len(rows),
            "sightings": len(sight),
            "tape": len(tape),
            "packages": len(pkgs),
        }
        log.info("pos_book.session", day=str(t), **out[str(t)])
        last_rows, last_sight, last_t = rows, sight, t
    refresh_players(session)
    if telegram and last_t is not None:
        _send(session, last_t, last_rows, last_sight)
    try:
        from trading_intel.scheduler import track

        n_rows = sum(v["lines"] for v in out.values())
        track.report(rows_attempted=n_rows, rows_written=n_rows)
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return out


def _send(session: Session, t: date, rows: list[dict], sight: list[dict]) -> None:
    from trading_intel.clients.telegram import TelegramClient

    players = list(session.execute(text("SELECT slug, name, status FROM flow_players")))
    names = {r[0]: r[1] for r in players}
    unnamed = [r[0] for r in players if r[2] == "unnamed"]
    cov = session.execute(
        text(
            "SELECT coverage_pct, vs_median_pct, status, empty_rth_slots, prints FROM tape_coverage_daily "
            "WHERE date = :d AND root = 'SPX*'"
        ),
        {"d": t},
    ).first()
    TelegramClient(get_settings()).send_message(
        card(t, rows, sight, names, unnamed, coverage_text(cov))
    )


def coverage_text(cov) -> str | None:
    """One line on how much of that session's tape we caught; works before any % exists (first weeks)."""
    if not cov:
        return None
    pct = cov[0] if cov[0] is not None else cov[1]
    size = f"{pct:.0f}% of normal" if pct is not None else f"{(cov[4] or 0):,} prints"
    return f"Tape captured: {size} ({cov[2]})" + (f" · empty: {cov[3]}" if cov[3] else "")


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="pos_book")
    ap.add_argument("--days", type=int, default=3, help="redo the newest N sessions (default 3)")
    ap.add_argument(
        "--backfill", action="store_true", help="rebuild every session since 2026-08-01, in order"
    )
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--name", nargs=2, metavar=("SLUG", "NAME"), help="give a repeat trader a name")
    a = ap.parse_args(argv)
    with make_session_factory(get_settings())() as s:
        if a.name:
            name_player(s, a.name[0], a.name[1])
            print("named", a.name[0], "->", a.name[1])
            return 0
        res = run(s, a.days, a.backfill, telegram=not a.no_telegram)
    for k, v in res.items():
        print(k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
