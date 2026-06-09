import time
from typing import Iterable

from backend.auth.store import auth_connection


def init_portfolio_db() -> None:
    with auth_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                coin_id TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_cost_usd REAL NOT NULL,
                last_price_usd REAL,
                last_value_usd REAL,
                last_pl_usd REAL,
                last_pl_percent REAL,
                last_priced_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(user_id, symbol),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user_symbol
                ON portfolio_holdings(user_id, symbol);

            CREATE TABLE IF NOT EXISTS portfolio_value_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                total_cost_usd REAL NOT NULL,
                total_value_usd REAL NOT NULL,
                total_pl_usd REAL NOT NULL,
                total_pl_percent REAL NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_created
                ON portfolio_value_snapshots(user_id, created_at);
            """
        )


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _cost_basis(row) -> float:
    return float(row["quantity"]) * float(row["average_cost_usd"])


def public_holding(row) -> dict:
    cost_basis = _cost_basis(row)
    return {
        "id": str(row["id"]),
        "symbol": row["symbol"],
        "coinId": row["coin_id"],
        "quantity": float(row["quantity"]),
        "averageCostUsd": float(row["average_cost_usd"]),
        "costBasisUsd": cost_basis,
        "currentPriceUsd": _float_or_none(row["last_price_usd"]),
        "currentValueUsd": _float_or_none(row["last_value_usd"]),
        "plUsd": _float_or_none(row["last_pl_usd"]),
        "plPercent": _float_or_none(row["last_pl_percent"]),
        "lastPricedAt": int(row["last_priced_at"]) if row["last_priced_at"] else None,
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
    }


def public_snapshot(row) -> dict:
    return {
        "id": str(row["id"]),
        "totalCostUsd": float(row["total_cost_usd"]),
        "totalValueUsd": float(row["total_value_usd"]),
        "totalPlUsd": float(row["total_pl_usd"]),
        "totalPlPercent": float(row["total_pl_percent"]),
        "createdAt": int(row["created_at"]),
    }


def list_portfolio_holdings(user_id: int | str) -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM portfolio_holdings
            WHERE user_id = ?
            ORDER BY symbol ASC
            """,
            (str(user_id),),
        ).fetchall()
    return [public_holding(row) for row in rows]


def list_portfolio_snapshots(user_id: int | str, limit: int = 120) -> list[dict]:
    safe_limit = max(1, min(int(limit or 120), 500))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM portfolio_value_snapshots
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (str(user_id), safe_limit),
        ).fetchall()
    return [public_snapshot(row) for row in reversed(rows)]


def latest_portfolio_snapshot(user_id: int | str) -> dict | None:
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM portfolio_value_snapshots
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (str(user_id),),
        ).fetchone()
    return public_snapshot(row) if row else None


def current_cost_summary(holdings: list[dict]) -> dict:
    total_cost = sum(float(holding["costBasisUsd"]) for holding in holdings)
    total_value = sum(
        float(holding["currentValueUsd"])
        if holding.get("currentValueUsd") is not None
        else float(holding["costBasisUsd"])
        for holding in holdings
    )
    total_pl = total_value - total_cost
    total_pl_percent = (total_pl / total_cost * 100) if total_cost else 0.0
    return {
        "totalCostUsd": total_cost,
        "totalValueUsd": total_value,
        "totalPlUsd": total_pl,
        "totalPlPercent": total_pl_percent,
        "createdAt": None,
    }


def portfolio_payload(user_id: int | str) -> dict:
    holdings = list_portfolio_holdings(user_id)
    snapshots = list_portfolio_snapshots(user_id)
    return {
        "holdings": holdings,
        "snapshots": snapshots,
        "summary": snapshots[-1] if snapshots else current_cost_summary(holdings),
    }


def upsert_portfolio_holdings(user_id: int | str, holdings: Iterable[dict]) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        for holding in holdings:
            symbol = str(holding["symbol"]).upper()
            coin_id = str(holding["coin_id"])
            quantity = float(holding["quantity"])
            average_cost = float(holding["average_cost_usd"])
            existing = conn.execute(
                """
                SELECT *
                FROM portfolio_holdings
                WHERE user_id = ? AND symbol = ?
                """,
                (str(user_id), symbol),
            ).fetchone()

            if existing:
                old_quantity = float(existing["quantity"])
                old_cost = float(existing["average_cost_usd"])
                new_quantity = old_quantity + quantity
                if new_quantity <= 0:
                    raise ValueError("Holding quantity must stay positive")
                new_average_cost = ((old_quantity * old_cost) + (quantity * average_cost)) / new_quantity
                last_price = existing["last_price_usd"]
                if last_price is not None:
                    last_value = new_quantity * float(last_price)
                    cost_basis = new_quantity * new_average_cost
                    last_pl = last_value - cost_basis
                    last_pl_percent = (last_pl / cost_basis * 100) if cost_basis else 0.0
                else:
                    last_value = None
                    last_pl = None
                    last_pl_percent = None
                conn.execute(
                    """
                    UPDATE portfolio_holdings
                    SET coin_id = ?,
                        quantity = ?,
                        average_cost_usd = ?,
                        last_value_usd = ?,
                        last_pl_usd = ?,
                        last_pl_percent = ?,
                        updated_at = ?
                    WHERE user_id = ? AND symbol = ?
                    """,
                    (
                        coin_id,
                        new_quantity,
                        new_average_cost,
                        last_value,
                        last_pl,
                        last_pl_percent,
                        now,
                        str(user_id),
                        symbol,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO portfolio_holdings
                        (user_id, symbol, coin_id, quantity, average_cost_usd, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(user_id), symbol, coin_id, quantity, average_cost, now, now),
                )


def delete_portfolio_holding(user_id: int | str, symbol: str) -> bool:
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM portfolio_holdings
            WHERE user_id = ? AND symbol = ?
            """,
            (str(user_id), symbol.upper()),
        )
    return cursor.rowcount > 0


def update_holding_valuations(
    user_id: int | str,
    price_by_symbol: dict[str, float],
    priced_at: int | None = None,
) -> None:
    now = int(time.time())
    priced_at = priced_at or now
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM portfolio_holdings
            WHERE user_id = ?
            """,
            (str(user_id),),
        ).fetchall()

        for row in rows:
            symbol = row["symbol"]
            if symbol not in price_by_symbol:
                continue
            price = float(price_by_symbol[symbol])
            quantity = float(row["quantity"])
            cost_basis = _cost_basis(row)
            current_value = quantity * price
            pl_usd = current_value - cost_basis
            pl_percent = (pl_usd / cost_basis * 100) if cost_basis else 0.0
            conn.execute(
                """
                UPDATE portfolio_holdings
                SET last_price_usd = ?,
                    last_value_usd = ?,
                    last_pl_usd = ?,
                    last_pl_percent = ?,
                    last_priced_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (price, current_value, pl_usd, pl_percent, priced_at, now, row["id"]),
            )


def create_portfolio_snapshot(user_id: int | str) -> dict | None:
    now = int(time.time())
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM portfolio_holdings
            WHERE user_id = ?
            ORDER BY symbol ASC
            """,
            (str(user_id),),
        ).fetchall()
        if not rows:
            return None

        total_cost = sum(_cost_basis(row) for row in rows)
        total_value = sum(
            float(row["last_value_usd"]) if row["last_value_usd"] is not None else _cost_basis(row)
            for row in rows
        )
        total_pl = total_value - total_cost
        total_pl_percent = (total_pl / total_cost * 100) if total_cost else 0.0

        cursor = conn.execute(
            """
            INSERT INTO portfolio_value_snapshots
                (user_id, total_cost_usd, total_value_usd, total_pl_usd, total_pl_percent, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), total_cost, total_value, total_pl, total_pl_percent, now),
        )
        row = conn.execute(
            """
            SELECT *
            FROM portfolio_value_snapshots
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return public_snapshot(row)
