# GitCoin Validation Guide

This document describes the two levels of validation in GitCoin:

1. **Full Chain Validation** — offline audit of the entire ledger state
2. **Per-TX Validation** — online verification of a single PR at merge time

---

## 1. Full Chain Validation

### What it checks

`validate_full.py` performs a complete, independent audit of the repository state. It does **not** trust `docs/ledger.json` as ground truth; it recomputes everything from the raw files on disk and the git history.

| # | Check | Details |
|---|-------|---------|
| 1 | **UTXO file integrity** | Every `utxo/*.json` is valid JSON, has all required fields, and the `txid` field matches the filename |
| 2 | **No double-spend** | No txid appears more than once; no two UTXOs share identical (owner, amount, created_at_block) |
| 3 | **Supply accounting** | Sum of all UTXO amounts matches the balance totals in `docs/ledger.json` |
| 4 | **Pubkey registry** | Every UTXO owner has an Ed25519 public key in `validators/pubkeys.json` |
| 5 | **Merkle root** | Recomputes the Merkle root from all txids sorted lexicographically and compares with `docs/ledger.json` |
| 6 | **Git TX history** | Walks every commit that touched `utxo/*.json` and verifies: (a) conservation of value, (b) no UTXO re-spent, (c) output txids match the sha256 derivation formula |

### How the Merkle root is computed

```
leaf_hash(txid)  = sha256(txid)
parent_hash(L,R) = sha256(L + R)
```

Txids are sorted lexicographically before hashing. If a level has an odd number of nodes, the last node is duplicated (Bitcoin-style).

### How output txids are derived

Every TRANSFER transaction derives its output txids deterministically so they cannot be forged:

```
tx_seed      = sha256(FROM + TO + AMOUNT + sorted_INPUT_TXIDS)  # called tx_nonce
output_to    = sha256(TO   + AMOUNT        + tx_nonce)
output_change= sha256(FROM + change_amount + tx_nonce + "change")
```

`validate_full.py` re-derives these values from the git commit subject line (`tx: FROM -> TO AMOUNT GTC`) for every historical TX commit and confirms the added UTXO files match.

### Running

```bash
# From the repository root
git pull origin main
python3 .github/scripts/validate_full.py
```

No extra dependencies required (standard library only).

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All checks passed — chain is valid |
| `1` | One or more checks failed — errors listed at the end of output |

### Example output

```
GitCoin Full Chain Validator
Repository: /path/to/gitcoin
HEAD:       a1b2c3d4...
Branch:     main

============================================================
  1. UTXO File Integrity
============================================================
  OK    5 UTXO files parsed successfully

============================================================
  2. No Double-Spend (txid uniqueness)
============================================================
  OK    No duplicate UTXO content fingerprints found

============================================================
  3. Supply Accounting
============================================================
  INFO  Total supply on disk: 50,000 GTC across 5 UTXOs
  OK    Supply matches ledger.json (50,000 GTC)

============================================================
  4. Pubkey Registry Coverage
============================================================
  OK    All 2 UTXO owners have registered public keys

============================================================
  5. Merkle Root Consistency
============================================================
  INFO  Computed Merkle root: 3a7f...
  OK    Merkle root matches ledger.json

============================================================
  6. Git TX History (conservation of value + txid derivation)
============================================================
  INFO  Found 8 commits touching utxo/
  INFO  Genesis UTXO d2d16c... +10000 GTC — 0376ed9e
  OK    All 8 TX commits pass history audit

============================================================
  RESULT: All checks passed — chain is valid
```

---

## 2. Per-TX Validation (PR-time)

### What it checks

`validate_tx.py` runs automatically via **`validate-tx.yml`** on every pull request that touches `utxo/` or `validators/`. It validates a single proposed transaction before it is merged.

#### TRANSFER (TX_VERSION: 1)

| # | Check | Details |
|---|-------|---------|
| 1 | **Required fields** | PR body contains `TX_VERSION`, `FROM`, `TO`, `AMOUNT`, `INPUT_TXIDS`, `OUTPUT_TO_TXID`, `SIGNATURE` |
| 2 | **Author match** | `FROM` equals the GitHub login of the PR author |
| 3 | **File scope** | PR only adds/deletes files in `utxo/*.json` |
| 4 | **Input ownership** | Each input UTXO exists on `main` and is owned by `FROM` |
| 5 | **Conservation of value** | Sum of input amounts equals sum of output amounts |
| 6 | **Output correctness** | Output UTXO `owner` and `amount` fields match `TO` and `AMOUNT` |
| 7 | **Ed25519 signature** | Canonical message signed by `FROM`'s registered public key |
| 8 | **Merkle root match** | `MERKLE_ROOT` in PR body matches `docs/ledger.json` on `main` |
| 9 | **Merkle inclusion proof** | Each input txid has a valid proof path to `MERKLE_ROOT` (O(log n)) |
| 10 | **Output txid derivation** | `OUTPUT_TO_TXID` and `OUTPUT_CHANGE_TXID` match the sha256 derivation formula |
| 11 | **created_at_block** | Output UTXO files have `created_at_block == tx_nonce` |

#### REGISTER_KEY (TX_VERSION: REGISTER_KEY)

| # | Check | Details |
|---|-------|---------|
| 1 | **File scope** | PR only modifies `validators/pubkeys.json` |
| 2 | **USERNAME match** | `USERNAME` field equals PR author |
| 3 | **Valid Ed25519 key** | `PUBLIC_KEY` decodes to exactly 32 bytes and loads as a valid Ed25519 public key |
| 4 | **Single addition** | Only one new entry is added to `pubkeys.json`; no existing entries are modified |

### Canonical message format (signed by sender)

```
TX_VERSION:1
FROM:<username>
TO:<username>
AMOUNT:<integer>
INPUT_TXIDS:<txid1>,<txid2>,...   # sorted lexicographically
OUTPUT_TO_TXID:<txid>
OUTPUT_CHANGE_TXID:<txid>         # omitted if no change
MEMO:<text>                       # omitted if empty
```

### Workflow trigger

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, reopened]
    paths:
      - 'utxo/**'
      - 'validators/**'
```

The workflow posts a `validate-tx/validate` commit status to the PR head:

- `success` — transaction is valid; PR author is prompted to post `/approve`
- `failure` — validation failed; reason posted as a PR comment

### Difference from full chain validation

| | `validate_full.py` | `validate_tx.py` |
|---|---|---|
| **Scope** | Entire ledger history | Single PR |
| **Trigger** | Manual (`git pull && python3 ...`) | Automatic (GitHub Actions on PR open) |
| **Merkle proof** | Recomputes root from disk | Verifies inclusion proof from PR body |
| **Signature check** | Not performed | Yes (Ed25519) |
| **Git history** | Yes (all commits) | No |
| **Dependency** | Standard library | `cryptography` (in Actions env) |

---

## Running both validators locally

```bash
# Full chain audit
git pull origin main
python3 .github/scripts/validate_full.py

# Single TX validation (simulate what the workflow does)
# Requires: PR_BODY and PR_AUTHOR env vars, plus pr_files.json and pr_head_content.json
PR_BODY="$(cat my_pr_body.txt)" PR_AUTHOR="volta2030" \
  python3 .github/scripts/validate_tx.py pr_files.json pr_head_content.json
```
