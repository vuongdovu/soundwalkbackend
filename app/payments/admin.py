"""
Payment admin - exports from submodules.

This file imports admin configurations from the ledger submodule
so Django's admin autodiscover can find them.
"""

from payments.ledger.admin import LedgerAccountAdmin, LedgerEntryAdmin

__all__ = ["LedgerAccountAdmin", "LedgerEntryAdmin"]
