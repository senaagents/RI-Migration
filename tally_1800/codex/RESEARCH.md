# Research Notes

## Official Tally Documentation

Tally documents the data-file roles for TallyPrime 3.0+:

- `Company.1800`, `AddlCmp.1800`, `CmpSave.1800`: company information,
  security control information, and F11 features.
- `Manager.1800`: master accounting information.
- `ExtMngr.1800`: edit log and dependency recomputation information.
- `LinkMgr.1800`: bill-wise details, order references, and similar link-master
  information.
- `TranMgr.1800`: transaction information.
- `SecTran.1800`: summary transactions, exchange activity, export summary, and
  return transactions.
- `CfgRept.1800`: saved report configurations.
- `Aggr.1800`: aggregate key/value information for precalculated statutory and
  report values.
- `VchStatus.1800`, `StatStatus.1800`: voucher and statutory status.
- `.TSF` files: database state, message queue, intent-message queue, access,
  exclusive access, and rollback/update coordination.

Source:
https://help.tallysolutions.com/data-migration-data-corruption-faq/

Tally's official integration routes are XML/HTTP and ODBC, both requiring
TallyPrime to run:

- XML interface: https://help.tallysolutions.com/xml-interface/
- ODBC extraction: https://help.tallysolutions.com/extracting-master-data-to-microsoft-excel-using-odbc-tally/

## TallyVault Clues

Elcomsoft published details for Tally ERP 9-era TallyVault `.900` files. Treat
these as clues, not as confirmed `.1800` facts:

- `.900` files larger than 512 bytes are encrypted when vaulting is enabled.
- Files are described as 512-byte pages.
- Each page starts with a 4-byte CRC over the remaining 508 bytes.
- After the CRC, offset 4 reportedly contains fixed little-endian `0x00000001`.
- Offset 12 reportedly often stores page number.
- Encryption is described as DES-like, 64-bit key, 8-byte blocks, CBC with zero IV.
- Key derivation is reportedly weak and direct from password.

Source:
https://blog.elcomsoft.com/2020/04/tally-erp-9-vault-how-to-not-implement-password-protection/

Elcomsoft's product page lists Tally Vault support for `.500`, `.900`, and
`.1800`, which is a signal that `.1800` vaulting is likely similar enough for
forensic tooling, but the public post does not disclose the `.1800` details.

Source:
https://www.elcomsoft.com/edpr.html

## Working Hypotheses

1. `.1800` is a page/block database, not one monolithic serialized object.
2. `.1800` may retain 512-byte page concepts, but this must be tested.
3. Vaulted `.1800` may encrypt pages/blocks independently.
4. Non-vaulted `.1800` may still be compressed or partly binary-encoded.
5. `TranMgr.1800`, `Manager.1800`, and `LinkMgr.1800` must be decoded together
   because transactions reference masters and link allocations.

