"""TradeLedger performance analytics engine.

Layers, from the bottom up:

  trade_math.py   one trade in, one TradeResult out (P&L, risk, R, outcome)
  metrics.py      a list of TradeResults in, one Summary out
  report.py       database rows in, a full PerformanceReport out (grouped)
  formatting.py   reports out to JSON-safe dicts and display strings

Nothing in this package imports Flask, and only report.build_report()
touches the database, so every calculation can be tested on its own.
"""

from .report import PerformanceReport, analyse, build_report

__all__ = ["PerformanceReport", "analyse", "build_report"]
