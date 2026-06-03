"""Cost tracking. Per-call cost is stored on ``calls.cost`` (total) with the
provider breakdown JSON in ``calls.cost_breakdown``. These helpers aggregate
for the dashboard and reports.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func

from voiceagent.db.models import Call


def total_cost(session) -> float:
    return float(session.query(func.coalesce(func.sum(Call.cost), 0.0)).scalar() or 0.0)


def cost_by_component(session) -> dict[str, float]:
    """Aggregate Retell ``product_costs`` across calls (best-effort)."""
    totals: dict[str, float] = {}
    rows = session.query(Call.cost_breakdown).filter(Call.cost_breakdown.isnot(None)).all()
    for (raw,) in rows:
        try:
            data: dict[str, Any] = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for product in data.get("product_costs", []) or []:
            name = product.get("product", "unknown")
            cost = product.get("cost", 0) or 0
            totals[name] = totals.get(name, 0.0) + float(cost) / 100.0  # cents -> dollars
    return totals
