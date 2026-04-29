"""Pure-binary RE toolkit for non-vaulted TallyPrime .1800 company data.

Reads `.1800` files directly without any Tally runtime / XML dependency and
emits a SQLite database with master records, vouchers, line items, and
allocation links.

Reference state of art (2026-04-27):
- Master records: 100% GUID match on the reference company (070525).
- Voucher headers: 100% GUID match on the reference company.
- Voucher dates: 100% on all 4 corpus companies.
- Voucher types: 95-99% on all 4 corpus companies.
- LinkMgr allocations: extracted on 4 companies.
- Voucher line items: in-progress (~90% ref recovery).

CLI:
    python3 -m tally1800 extract <company_dir> -o out.sqlite
"""

__version__ = "0.1.0"
