"""
account_engine.py — Account bookkeeping shared by backtest and live signal.

Extracted from account_simulator.py so that account-aware filtering
is available to both the backtest path (historical simulation) and
the live signal path (check today's directives against current snapshot).

No dependency on backtest. No dependency on strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AccountState:
    """Account state at a point in time.

    All values are in currency units (e.g. CNY). Shares are fractional
    (ETFs/mutual funds allow fractional shares).
    """
    cash: float = 0.0
    shares: float = 0.0
    avg_cost: float = 0.0          # weighted average entry price
    total_invested: float = 0.0     # total cash spent on current position

    @property
    def is_invested(self) -> bool:
        return self.shares > 0

    @property
    def position_value(self, price: float = None) -> float:
        """Current market value of held shares.

        Without price arg, returns total cost basis (stale in backtest loop).
        For live pricing, pass current price.
        """
        return self.shares * price if price is not None else self.shares

    @property
    def total_equity(self, price: float = None) -> float:
        """Cash + market value of position."""
        pv = self.position_value(price) if price is not None else 0.0
        return self.cash + pv


@dataclass
class ExecutionResult:
    """Outcome of applying one trade directive to an AccountState."""
    executed: bool
    status: str                    # "EXECUTED" | "SKIPPED_NO_CASH" | "SKIPPED_NO_SHARES" | "PARTIAL"
    shares_delta: float = 0.0
    amt_delta: float = 0.0         # positive = cash inflow (sell), negative = outflow (buy)
    new_account: AccountState | None = None
    note: str = ""


def execute_trade(
    account: AccountState,
    verb: str,
    fraction: float,
    price: float,
    label: str = "",
) -> tuple[AccountState, ExecutionResult]:
    """Apply ONE trade directive to an account. Pure function.

    Fraction semantics (no leverage, 0~1):
      BUY:   amt = account.cash × fraction  → fraction of available cash
      SELL:  sold = account.shares × fraction → fraction of current position
      CLOSE: close entire position

    Args:
        account:   Current account state.
        verb:      "BUY", "SELL", or "CLOSE".
        fraction:  0~1. For BUY: % of cash. For SELL: % of shares.
        price:     Current price per share.
        label:     Human-readable trade description (for result).

    Returns:
        (new_account, ExecutionResult)
    """
    new_account = AccountState(
        cash=account.cash,
        shares=account.shares,
        avg_cost=account.avg_cost,
        total_invested=account.total_invested,
    )

    if verb == "BUY":
        if account.cash <= 0:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_CASH",
                note=f"BUY {label}: no cash available",
            )
        amt = account.cash * fraction
        if amt <= 0:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_CASH",
                note=f"BUY {label}: fractional amount is zero",
            )
        if amt > account.cash:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_CASH",
                note=f"BUY {label}: insufficient cash (need {amt:.2f}, have {account.cash:.2f})",
            )
        bought = amt / price
        new_account.shares += bought
        new_account.cash -= amt
        new_account.total_invested += amt
        if new_account.shares > 0:
            new_account.avg_cost = new_account.total_invested / new_account.shares
        return new_account, ExecutionResult(
            executed=True, status="EXECUTED",
            shares_delta=bought, amt_delta=-amt,
            new_account=new_account,
            note=f"BUY {label}: {bought:.4f} shares @ {price:.4f}, cost {amt:.2f}",
        )

    elif verb == "SELL":
        if account.shares <= 0:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_SHARES",
                note=f"SELL {label}: no shares to sell",
            )
        sold = account.shares * fraction
        if sold <= 0:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_SHARES",
                note=f"SELL {label}: fractional amount is zero",
            )
        val = sold * price
        new_account.shares -= sold
        new_account.cash += val
        if new_account.shares > 0:
            cost_relief = account.total_invested * fraction
            new_account.total_invested = max(0.0, account.total_invested - cost_relief)
            new_account.avg_cost = new_account.total_invested / new_account.shares
        else:
            new_account.total_invested = 0.0
            new_account.avg_cost = 0.0
        return new_account, ExecutionResult(
            executed=True, status="EXECUTED",
            shares_delta=-sold, amt_delta=val,
            new_account=new_account,
            note=f"SELL {label}: {sold:.4f} shares @ {price:.4f}, proceeds {val:.2f}",
        )

    elif verb == "CLOSE":
        if account.shares <= 0:
            return new_account, ExecutionResult(
                executed=False, status="SKIPPED_NO_SHARES",
                note=f"CLOSE {label}: no shares to close",
            )
        val = account.shares * price
        new_account.shares = 0.0
        new_account.cash += val
        new_account.total_invested = 0.0
        new_account.avg_cost = 0.0
        return new_account, ExecutionResult(
            executed=True, status="EXECUTED",
            shares_delta=-account.shares, amt_delta=val,
            new_account=new_account,
            note=f"CLOSE {label}: {account.shares:.4f} shares @ {price:.4f}, proceeds {val:.2f}",
        )

    else:
        return new_account, ExecutionResult(
            executed=False, status="SKIPPED_NO_SHARES",
            note=f"Unknown verb: {verb}",
        )


def filter_trades(
    directives: list[dict[str, Any]],
    account: AccountState,
    price: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter today's trade directives against current account state.

    Used by live signal path: given all directives produced by a strategy
    for today, determine which are executable given the current account
    snapshot from logs/positions/.

    Args:
        directives: List of dicts with keys: verb, fraction, label, signal.
        account:    Current account snapshot.
        price:      Current price per share.

    Returns:
        (executable, skipped)
        executable: List of dicts that can be executed + execution note.
        skipped:    List of dicts that cannot be executed + skip reason.
    """
    executable = []
    skipped = []
    current = AccountState(
        cash=account.cash, shares=account.shares,
        avg_cost=account.avg_cost, total_invested=account.total_invested,
    )

    for d in directives:
        verb = d.get("verb", "HOLD")
        fraction = float(d.get("fraction", 0.0))
        label = d.get("label", "")

        if verb == "HOLD":
            skipped.append({**d, "execution": "SKIPPED", "note": "HOLD — no action"})
            continue

        new_account, result = execute_trade(current, verb, fraction, price, label)
        d_result = {**d, "execution": result.status, "note": result.note}

        if result.executed:
            current = new_account
            d_result["shares_delta"] = result.shares_delta
            d_result["amt_delta"] = result.amt_delta
            executable.append(d_result)
        else:
            skipped.append(d_result)

    return executable, skipped
