#!/usr/bin/env python3
"""
GitCoin Full Chain Validator

Performs a complete, independent audit of the ledger state on disk:

  1. UTXO Integrity     — every utxo/*.json is well-formed and internally consistent
  2. No Double-Spend    — each txid appears exactly once across all UTXO files
  3. Supply Accounting  — total supply matches the sum of all UTXO amounts
  4. Pubkey Registry    — every owner in utxo/ has a registered Ed25519 public key
  5. Merkle Root        — recomputes the Merkle root from all current UTXO txids
                          and compares with docs/ledger.json
  6. Git TX History     — walks every merge commit that touched utxo/*.json and
                          verifies:
                            a. Conservation of value  (inputs == outputs)
                            b. No UTXO was re-spent after being consumed
                            c. Output txids match the sha256 derivation formula

Run from the repository root after `git pull`:

  python3 .github/scripts/validate_full.py

Exit codes:
  0 — all checks pass
  1 — one or more checks failed (details printed to stdout)
"""

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ERRORS: list[str] = []
WARNINGS: list[str] = []


def error(msg: str) -> None:
    print(f"  FAIL  {msg}")
    ERRORS.append(msg)


def warn(msg: str) -> None:
    print(f"  WARN  {msg}")
    WARNINGS.append(msg)


def ok(msg: str) -> None:
    print(f"  OK    {msg}")


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def merkle_node(a: str, b: str) -> str:
    return hashlib.sha256((a + b).encode()).hexdigest()


def build_merkle_root(txids: list[str]) -> str:
    """Bitcoin-style Merkle root from a sorted list of txids."""
    if not txids:
        return hashlib.sha256(b'').hexdigest()
    level = [hashlib.sha256(t.encode()).hexdigest() for t in txids]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [merkle_node(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def pages_url_from_remote() -> str | None:
    """
    Derive the GitHub Pages ledger.json URL from the git remote origin URL.
    https://github.com/USER/REPO  ->  https://USER.github.io/REPO/ledger.json
    git@github.com:USER/REPO.git  ->  https://USER.github.io/REPO/ledger.json
    """
    remote = git('remote', 'get-url', 'origin')
    if not remote:
        return None
    import re as _re
    m = _re.search(r'github\.com[:/]([^/]+)/([^/.]+)', remote)
    if not m:
        return None
    user, repo = m.group(1), m.group(2)
    return f'https://{user}.github.io/{repo}/ledger.json'


def fetch_remote_ledger() -> dict | None:
    """Fetch ledger.json from GitHub Pages. Returns parsed dict or None on failure."""
    url = pages_url_from_remote()
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'gitcoin-validate-full/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        print(f"  INFO  Fetched remote ledger.json from {url}")
        return data
    except Exception as e:
        print(f"  WARN  Could not fetch remote ledger.json ({url}): {e}")
        return None


def load_ledger() -> dict | None:
    """
    Load ledger.json: prefer a non-placeholder local file,
    otherwise fetch from GitHub Pages.
    Returns None if unavailable from both sources.
    """
    local_path = Path('docs/ledger.json')
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text())
            if data.get('balances') or data.get('merkle_root'):
                return data  # local file is real
        except (json.JSONDecodeError, ValueError):
            pass
    # Local is placeholder or missing — try remote
    return fetch_remote_ledger()


def git(*args: str) -> str:
    r = subprocess.run(
        ['git'] + list(args),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    return r.stdout.strip() if r.returncode == 0 else ''


# ---------------------------------------------------------------------------
# Check 1 — UTXO file integrity
# ---------------------------------------------------------------------------

def check_utxo_integrity() -> tuple[dict[str, dict], list[str]]:
    """
    Returns (utxos_by_txid, all_txids_sorted).
    utxos_by_txid: txid -> parsed UTXO dict for valid files.
    """
    section("1. UTXO File Integrity")
    utxo_dir = Path('utxo')
    utxos: dict[str, dict] = {}
    required_fields = {'txid', 'owner', 'amount', 'unit', 'created_at_block', 'created_at_height'}

    for f in sorted(utxo_dir.glob('*.json')):
        if f.name == '.gitkeep':
            continue
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            error(f"{f.name}: invalid JSON — {e}")
            continue

        missing = required_fields - set(data.keys())
        if missing:
            error(f"{f.name}: missing fields {missing}")
            continue

        # txid must match filename
        expected_txid = f.stem
        if data['txid'] != expected_txid:
            error(f"{f.name}: txid '{data['txid']}' does not match filename")

        # amount must be positive integer
        try:
            amt = int(data['amount'])
            if amt <= 0:
                error(f"{f.name}: amount must be positive, got {amt}")
        except (ValueError, TypeError):
            error(f"{f.name}: amount '{data['amount']}' is not a valid integer")
            continue

        # unit must be GTC
        if data['unit'] != 'GTC':
            error(f"{f.name}: unexpected unit '{data['unit']}'")

        utxos[expected_txid] = data

    ok(f"{len(utxos)} UTXO files parsed successfully")
    return utxos, sorted(utxos.keys())


# ---------------------------------------------------------------------------
# Check 2 — No double-spend (txid uniqueness)
# ---------------------------------------------------------------------------

def check_no_double_spend(utxos: dict[str, dict]) -> None:
    section("2. No Double-Spend (txid uniqueness)")
    # txids are the dict keys, so duplicates are impossible at the filesystem level.
    # We verify txid field inside each file matches the key (already done above).
    # Additionally verify no two UTXOs share the same (owner, amount, created_at_block).
    seen_content: dict[tuple, str] = {}
    duplicates = 0
    for txid, u in utxos.items():
        key = (u.get('owner'), u.get('amount'), u.get('created_at_block'))
        if key in seen_content:
            error(f"Possible duplicate: {txid[:16]}... and {seen_content[key][:16]}... share identical content fingerprint")
            duplicates += 1
        else:
            seen_content[key] = txid
    if duplicates == 0:
        ok("No duplicate UTXO content fingerprints found")


# ---------------------------------------------------------------------------
# Check 3 — Supply accounting
# ---------------------------------------------------------------------------

def check_supply(utxos: dict[str, dict], ledger: dict | None) -> int:
    section("3. Supply Accounting")
    total = sum(int(u.get('amount', 0)) for u in utxos.values())
    print(f"  INFO  Total supply on disk: {total:,} GTC across {len(utxos)} UTXOs")

    if ledger is None:
        print("  SKIP  ledger.json unavailable locally and from GitHub Pages")
    else:
        ledger_supply = sum(ledger.get('balances', {}).values())
        if ledger_supply != total:
            error(
                f"Supply mismatch: utxo/ sums to {total:,} GTC "
                f"but ledger.json balances sum to {ledger_supply:,} GTC"
            )
        else:
            ok(f"Supply matches ledger.json ({total:,} GTC)")

    return total


# ---------------------------------------------------------------------------
# Check 4 — Pubkey registry coverage
# ---------------------------------------------------------------------------

def check_pubkey_registry(utxos: dict[str, dict]) -> dict[str, str]:
    section("4. Pubkey Registry Coverage")
    pubkeys_path = Path('validators/pubkeys.json')
    if not pubkeys_path.exists():
        error("validators/pubkeys.json not found")
        return {}

    try:
        pubkeys: dict[str, str] = json.loads(pubkeys_path.read_text())
    except json.JSONDecodeError as e:
        error(f"validators/pubkeys.json is not valid JSON: {e}")
        return {}

    owners = {u.get('owner', '') for u in utxos.values() if u.get('owner')}
    unregistered = owners - set(pubkeys.keys())
    if unregistered:
        for o in sorted(unregistered):
            warn(f"Owner '{o}' holds GTC but has no registered public key (cannot spend until key is registered)")
    else:
        ok(f"All {len(owners)} UTXO owners have registered public keys")

    return pubkeys


# ---------------------------------------------------------------------------
# Check 5 — Merkle root consistency
# ---------------------------------------------------------------------------

def check_merkle_root(txids_sorted: list[str], ledger: dict | None) -> None:
    section("5. Merkle Root Consistency")
    computed_root = build_merkle_root(txids_sorted)
    print(f"  INFO  Computed Merkle root: {computed_root}")

    if ledger is None:
        print("  SKIP  ledger.json unavailable locally and from GitHub Pages")
        return

    stored_root = ledger.get('merkle_root', '')
    if not stored_root:
        print("  SKIP  ledger.json has no merkle_root field")
    elif computed_root != stored_root:
        error(
            f"Merkle root mismatch:\n"
            f"         computed: {computed_root}\n"
            f"         ledger  : {stored_root}\n"
            f"         (utxo/ was modified without regenerating ledger.json, "
            f"or ledger.json is stale)"
        )
    else:
        ok("Merkle root matches ledger.json")


# ---------------------------------------------------------------------------
# Check 6 — Git TX history (conservation of value + txid derivation)
# ---------------------------------------------------------------------------

def get_utxo_content_at(commit: str, path: str) -> dict | None:
    """Read a file's content at a specific commit."""
    raw = git('show', f'{commit}:{path}')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def check_git_history() -> None:
    section("6. Git TX History (conservation of value + txid derivation)")

    # Get all commits that touched utxo/*.json, oldest first
    log = git('log', '--reverse', '--pretty=format:%H|%s', '--diff-filter=AD', '--', 'utxo/*.json')
    if not log:
        print("  SKIP  No utxo commits found in git history")
        return

    commits = []
    for line in log.splitlines():
        if '|' not in line:
            continue
        h, _, subject = line.partition('|')
        commits.append((h.strip(), subject.strip()))

    print(f"  INFO  Found {len(commits)} commits touching utxo/")

    # Track globally consumed txids to catch re-spend across commits
    globally_consumed: set[str] = set()

    tx_errors = 0
    for commit_hash, subject in commits:
        # Get the diff: which utxo files were added and deleted in this commit
        diff_raw = git('diff-tree', '--no-commit-id', '-r', '--name-status', commit_hash)
        added: dict[str, dict] = {}    # txid -> UTXO content (new outputs)
        deleted: dict[str, dict] = {}  # txid -> UTXO content (consumed inputs)

        parent = git('rev-parse', f'{commit_hash}^') or 'ROOT'

        for diff_line in diff_raw.splitlines():
            parts = diff_line.split('\t')
            if len(parts) < 2:
                continue
            status, fpath = parts[0], parts[1]
            if not fpath.startswith('utxo/') or not fpath.endswith('.json'):
                continue
            txid = Path(fpath).stem
            if status == 'A':
                content = get_utxo_content_at(commit_hash, fpath)
                if content:
                    added[txid] = content
            elif status == 'D':
                content = get_utxo_content_at(parent, fpath) if parent != 'ROOT' else None
                if content:
                    deleted[txid] = content

        if not added and not deleted:
            continue

        label = f"{commit_hash[:12]} ({subject[:40]})"

        # Genesis commits (only additions, no deletions) are valid — skip value check
        if not deleted:
            for txid, utxo in added.items():
                amt = int(utxo.get('amount', 0))
                print(f"  INFO  Genesis UTXO {txid[:16]}... +{amt:,} GTC — {commit_hash[:12]}")
            continue

        # --- Conservation of value ---
        input_total = sum(int(u.get('amount', 0)) for u in deleted.values())
        output_total = sum(int(u.get('amount', 0)) for u in added.values())
        if input_total != output_total:
            error(
                f"{label}: value not conserved "
                f"(inputs={input_total:,} GTC, outputs={output_total:,} GTC)"
            )
            tx_errors += 1

        # --- No re-spend ---
        for txid in deleted:
            if txid in globally_consumed:
                error(f"{label}: UTXO '{txid[:16]}...' was already consumed (double-spend)")
                tx_errors += 1
            globally_consumed.add(txid)

        # --- Output txid derivation check ---
        # Reconstruct tx_nonce from the input set and match output txids.
        # We infer FROM/TO/AMOUNT from the subject line "tx: FROM -> TO AMOUNT GTC"
        m = re.match(r'tx:\s+(\S+)\s+[→\->]+\s+(\S+)\s+(\d+)\s+GTC', subject)
        if m:
            from_user, to_user, amount_str = m.group(1), m.group(2), m.group(3)
            amount = int(amount_str)
            sorted_inputs = sorted(deleted.keys())
            tx_seed = f"{from_user}{to_user}{amount}{','.join(sorted_inputs)}"
            tx_nonce = hashlib.sha256(tx_seed.encode()).hexdigest()

            expected_to_txid = hashlib.sha256(f"{to_user}{amount}{tx_nonce}".encode()).hexdigest()
            if expected_to_txid not in added:
                # Check change output derivation
                change_amount = input_total - amount
                expected_change_txid = hashlib.sha256(
                    f"{from_user}{change_amount}{tx_nonce}change".encode()
                ).hexdigest()
                if expected_to_txid not in added and expected_change_txid not in added:
                    error(
                        f"{label}: output txids do not match derivation formula "
                        f"(expected to={expected_to_txid[:16]}...)"
                    )
                    tx_errors += 1
            else:
                pass  # derivation matched
        else:
            print(f"  SKIP  {label}: subject does not match 'tx: FROM -> TO AMOUNT GTC' — skipping derivation check")

    if tx_errors == 0:
        ok(f"All {len(commits)} TX commits pass history audit")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("GitCoin Full Chain Validator")
    print(f"Repository: {git('rev-parse', '--show-toplevel') or Path.cwd()}")
    print(f"HEAD:       {git('rev-parse', 'HEAD')}")
    print(f"Branch:     {git('rev-parse', '--abbrev-ref', 'HEAD')}")

    print("\nLoading ledger.json (local → GitHub Pages fallback)...")
    ledger = load_ledger()
    if ledger is None:
        print("  WARN  ledger.json unavailable — supply and Merkle root checks will be skipped")

    utxos, txids_sorted = check_utxo_integrity()
    check_no_double_spend(utxos)
    check_supply(utxos, ledger)
    check_pubkey_registry(utxos)
    check_merkle_root(txids_sorted, ledger)
    check_git_history()

    print(f"\n{'='*60}")
    if WARNINGS:
        print(f"  WARNINGS: {len(WARNINGS)}")
        for i, w in enumerate(WARNINGS, 1):
            print(f"    [W{i}] {w}")
    if ERRORS:
        print(f"  RESULT: {len(ERRORS)} error(s) found")
        for i, e in enumerate(ERRORS, 1):
            print(f"    [E{i}] {e}")
        sys.exit(1)
    else:
        print(f"  RESULT: All checks passed — chain is valid")
        sys.exit(0)


if __name__ == '__main__':
    main()
