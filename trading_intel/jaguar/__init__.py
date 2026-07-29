"""Jaguar daily-brief lane.

Turns the three core Jaguar emails (JaguarLive, First Read, Trade Alert) — read
straight from Gmail — into the signal-first daily brief: his trades & the flow he's
following, our tape's cross-check on each named print, a defined-risk structure, his
thinking, S&P breadth, what changed, and the macro through-line.

Deterministic parse (``parse``) grounds every ticker / contract / size so nothing is
hallucinated; the local LLM (``extract``, Ollama — rule 7) only condenses prose. His
calls are relayed descriptively and the ⚡ structures are illustrative analysis, never
an automated signal (FlashAlpha rule 4).
"""
