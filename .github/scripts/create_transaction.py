#!/usr/bin/env python3
"""
GitCoin Transaction Builder

Builds and signs a GTC transfer transaction.
Outputs the PR body and the exact file operations needed.

Usage:
  pip install cryptography
  python3 create_transaction.py

You will be prompted for:
  - Your GitHub username
  - Your private key (base64url)
  - Recipient GitHub username
  - Amount to send (GTC)
  - UTXO txids to spend (comma-separated)
  - MEMO (optional)

The script reads input UTXO files from the local utxo/ directory
(run this from the root of your forked repo after a git pull).
"""

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    print("ERROR: Please install the cryptography library:")
    print("  pip install cryptography")
    sys.exit(1)


def b64url_decode(s: str) -> bytes:
    s = s.strip()
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def merkle_node(a: str, b: str) -> str:
    return hashlib.sha256((a + b).encode()).hexdigest()


def build_merkle_tree(leaves: list) -> list:
    """Returns tree as list of levels, level[0] = leaf hashes."""
    if not leaves:
        return [[hashlib.sha256(b'').hexdigest()]]
    level = [hashlib.sha256(leaf.encode()).hexdigest() for leaf in leaves]
    levels = [level]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level = level + [level[-1]]
        level = [merkle_node(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        levels.append(level)
    return levels


def merkle_proof(txid: str, txids_sorted: list) -> list:
    """
    Compute Merkle inclusion proof for txid.
    Returns list of {"sibling": hash, "direction": "left"|"right"} dicts.
    """
    levels = build_merkle_tree(txids_sorted)
    leaf_hash = hashlib.sha256(txid.encode()).hexdigest()

    proof = []
    current = leaf_hash
    for level in levels[:-1]:  # skip root level
        padded = level + [level[-1]] if len(level) % 2 == 1 else level
        idx = padded.index(current)
        if idx % 2 == 0:
            sibling = padded[idx + 1]
            direction = "right"
        else:
            sibling = padded[idx - 1]
            direction = "left"
        proof.append({"sibling": sibling, "direction": direction})
        current = merkle_node(padded[idx], sibling) if direction == "right" else merkle_node(sibling, padded[idx])
    return proof


def fetch_ledger() -> dict:
    """Fetch ledger.json from GitHub Pages to get current merkle_root and utxo_txids."""
    import urllib.request
    url = "https://gitledger.github.io/gitcoin/ledger.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Warning: Could not fetch ledger.json from Pages ({e}). Falling back to local utxo/ scan.")
        return {}


def get_utxo_txids_from_local() -> list:
    """Fallback: build sorted txid list from local utxo/ directory."""
    utxo_dir = Path('utxo')
    txids = []
    for f in sorted(utxo_dir.glob('*.json')):
        try:
            utxo = json.loads(f.read_text())
            txid = utxo.get('txid', '').strip()
            if txid:
                txids.append(txid)
        except (json.JSONDecodeError, ValueError):
            pass
    return sorted(txids)


def make_txid(owner: str, amount: int, block_hash: str) -> str:
    data = f"{owner}{amount}{block_hash}"
    return hashlib.sha256(data.encode()).hexdigest()


def canonical_message(tx: dict) -> str:
    input_txids = sorted(t.strip() for t in tx['INPUT_TXIDS'].split(',') if t.strip())
    lines = [
        f"TX_VERSION:{tx['TX_VERSION']}",
        f"FROM:{tx['FROM']}",
        f"TO:{tx['TO']}",
        f"AMOUNT:{tx['AMOUNT']}",
        f"INPUT_TXIDS:{','.join(input_txids)}",
        f"OUTPUT_TO_TXID:{tx['OUTPUT_TO_TXID']}",
    ]
    if tx.get('OUTPUT_CHANGE_TXID'):
        lines.append(f"OUTPUT_CHANGE_TXID:{tx['OUTPUT_CHANGE_TXID']}")
    if tx.get('MEMO'):
        lines.append(f"MEMO:{tx['MEMO']}")
    return '\n'.join(lines)


def list_owned_utxos(owner: str) -> list:
    utxo_dir = Path('utxo')
    owned = []
    for f in sorted(utxo_dir.glob('*.json')):
        try:
            utxo = json.loads(f.read_text())
            if utxo.get('owner') == owner:
                owned.append(utxo)
        except (json.JSONDecodeError, ValueError):
            pass
    return owned


def main():
    print("=" * 60)
    print("GitCoin Transaction Builder")
    print("=" * 60)
    print()

    from_user = input("Your GitHub username: ").strip()

    # Show owned UTXOs
    owned = list_owned_utxos(from_user)
    if not owned:
        print(f"\nERROR: No UTXOs found for '{from_user}' in utxo/")
        print("Make sure you are on the latest main branch (git pull origin main)")
        sys.exit(1)

    total_balance = sum(int(u.get('amount', 0)) for u in owned)
    print(f"\nYour UTXOs ({len(owned)} total, {total_balance:,} GTC):")
    print(f"  {'#':<4} {'txid':<20}  {'amount':>18}")
    print(f"  {'-'*4} {'-'*20}  {'-'*18}")
    for i, u in enumerate(owned, 1):
        txid_short = u['txid'][:16] + '...'
        print(f"  {i:<4} {txid_short:<20}  {int(u.get('amount',0)):>16,} GTC")
    print()

    private_key_b64 = input("Your private key (base64url): ").strip()
    to_user = input("Recipient GitHub username: ").strip()
    amount_str = input("Amount to send (GTC): ").strip()

    # Smart UTXO selection prompt
    if len(owned) == 1:
        default_txid = owned[0]['txid']
        input_txids_str = input(f"UTXO txids to spend [Enter for #{1}: {default_txid[:16]}...]: ").strip()
        if not input_txids_str:
            input_txids_str = default_txid
    else:
        print("Enter txid numbers (e.g. 1  or  1,2) or full txids:")
        input_txids_str = input("UTXO txids to spend: ").strip()
        # Allow number shortcuts like "1" or "1,2"
        resolved = []
        for part in input_txids_str.split(','):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(owned):
                    resolved.append(owned[idx]['txid'])
                else:
                    print(f"ERROR: No UTXO #{part}")
                    sys.exit(1)
            else:
                resolved.append(part)
        input_txids_str = ','.join(resolved)

    memo = input("Memo (optional, press Enter to skip): ").strip()

    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        print("ERROR: Amount must be a positive integer")
        sys.exit(1)

    input_txids = [t.strip() for t in input_txids_str.split(',') if t.strip()]
    if not input_txids:
        print("ERROR: Must provide at least one input UTXO txid")
        sys.exit(1)

    # Load private key
    try:
        private_bytes = b64url_decode(private_key_b64)
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    except Exception as e:
        print(f"ERROR: Invalid private key: {e}")
        sys.exit(1)

    # Read input UTXOs from local utxo/ directory
    input_total = 0
    latest_block_hash = '0' * 64
    for txid in input_txids:
        utxo_path = Path(f"utxo/{txid}.json")
        if not utxo_path.exists():
            print(f"ERROR: UTXO file not found: utxo/{txid}.json")
            print("Make sure you have pulled the latest main branch.")
            sys.exit(1)
        utxo = json.loads(utxo_path.read_text())
        if utxo.get('owner') != from_user:
            print(f"ERROR: UTXO {txid} is owned by '{utxo.get('owner')}', not '{from_user}'")
            sys.exit(1)
        input_total += int(utxo.get('amount', 0))
        latest_block_hash = utxo.get('created_at_block', latest_block_hash)

    if input_total < amount:
        print(f"ERROR: Insufficient balance. Inputs total {input_total} GTC, need {amount} GTC.")
        sys.exit(1)

    change_amount = input_total - amount

    # Compute output txids
    # Use a block hash placeholder — the actual created_at_block will be the merge commit
    # Use a deterministic hash based on the transaction content for the placeholder
    tx_seed = f"{from_user}{to_user}{amount}{','.join(sorted(input_txids))}"
    tx_nonce = hashlib.sha256(tx_seed.encode()).hexdigest()

    output_to_txid = hashlib.sha256(f"{to_user}{amount}{tx_nonce}".encode()).hexdigest()
    output_change_txid = None
    if change_amount > 0:
        output_change_txid = hashlib.sha256(f"{from_user}{change_amount}{tx_nonce}change".encode()).hexdigest()

    tx = {
        'TX_VERSION': '1',
        'FROM': from_user,
        'TO': to_user,
        'AMOUNT': str(amount),
        'INPUT_TXIDS': ','.join(sorted(input_txids)),
        'OUTPUT_TO_TXID': output_to_txid,
    }
    if output_change_txid:
        tx['OUTPUT_CHANGE_TXID'] = output_change_txid
    if memo:
        tx['MEMO'] = memo

    # Sign the canonical message
    msg = canonical_message(tx)
    signature_bytes = private_key.sign(msg.encode('utf-8'))
    signature_b64 = base64.urlsafe_b64encode(signature_bytes).decode().rstrip('=')
    tx['SIGNATURE'] = signature_b64

    # Compute Merkle inclusion proof for each input UTXO
    print("\nFetching current UTXO Merkle root...")
    ledger = fetch_ledger()
    if ledger.get('utxo_txids') and ledger.get('merkle_root'):
        utxo_txids_sorted = ledger['utxo_txids']
        merkle_root = ledger['merkle_root']
        source = "ledger.json (Pages)"
    else:
        utxo_txids_sorted = get_utxo_txids_from_local()
        from update_ledger import build_merkle_tree as _bmt
        root_level = _bmt(utxo_txids_sorted)
        merkle_root = root_level[-1][0] if root_level else hashlib.sha256(b'').hexdigest()
        source = "local utxo/ scan"

    print(f"  Merkle root: {merkle_root[:16]}... (source: {source})")

    proofs = {}
    for txid in input_txids:
        if txid not in utxo_txids_sorted:
            print(f"  WARNING: txid {txid[:16]}... not found in UTXO set from {source}.")
            print("           The ledger.json may be stale. Run update-pages workflow first.")
            proofs[txid] = []
        else:
            proofs[txid] = merkle_proof(txid, utxo_txids_sorted)

    # Encode proofs as compact JSON string for PR body
    proof_json = json.dumps(proofs, separators=(',', ':'))

    # Build output UTXO JSON files
    # Note: created_at_block is set to the nonce placeholder; the real block hash
    # will differ after merge. The validator checks amounts/owners, not the block hash.
    output_to_utxo = {
        "txid": output_to_txid,
        "owner": to_user,
        "amount": amount,
        "unit": "GTC",
        "created_at_block": tx_nonce,
        "created_at_height": 0
    }
    output_change_utxo = None
    if output_change_txid:
        output_change_utxo = {
            "txid": output_change_txid,
            "owner": from_user,
            "amount": change_amount,
            "unit": "GTC",
            "created_at_block": tx_nonce,
            "created_at_height": 0
        }

    # Print the PR body
    print()
    print("=" * 60)
    print("STEP 1 — Copy this as your PR body:")
    print("=" * 60)
    pr_body_lines = [
        f"TX_VERSION: {tx['TX_VERSION']}",
        f"FROM: {tx['FROM']}",
        f"TO: {tx['TO']}",
        f"AMOUNT: {tx['AMOUNT']}",
        f"INPUT_TXIDS: {tx['INPUT_TXIDS']}",
        f"OUTPUT_TO_TXID: {tx['OUTPUT_TO_TXID']}",
    ]
    if output_change_txid:
        pr_body_lines.append(f"OUTPUT_CHANGE_TXID: {tx['OUTPUT_CHANGE_TXID']}")
    if memo:
        pr_body_lines.append(f"MEMO: {tx['MEMO']}")
    pr_body_lines.append(f"SIGNATURE: {signature_b64}")
    pr_body_lines.append(f"MERKLE_ROOT: {merkle_root}")
    pr_body_lines.append(f"MERKLE_PROOF: {proof_json}")
    print('\n'.join(pr_body_lines))

    print()
    print("=" * 60)
    print("STEP 2 — Make these file changes in your fork before creating the PR:")
    print("=" * 60)
    print()
    for txid in input_txids:
        print(f"  DELETE:  utxo/{txid}.json")
    print()
    to_utxo_path = f"utxo/{output_to_txid}.json"
    print(f"  CREATE:  {to_utxo_path}")
    print(f"  Content: {json.dumps(output_to_utxo, indent=4)}")
    print()
    if output_change_utxo:
        change_utxo_path = f"utxo/{output_change_txid}.json"
        print(f"  CREATE:  {change_utxo_path}")
        print(f"  Content: {json.dumps(output_change_utxo, indent=4)}")
        print()

    print("=" * 60)
    print("STEP 3 — Create a PR from your fork to main with the above file")
    print("  changes and PR body. The title should be:")
    print(f"  tx: {from_user} → {to_user} {amount} GTC")
    print("=" * 60)

    # Auto-write output UTXO files
    Path(to_utxo_path).write_text(json.dumps(output_to_utxo, indent=2))
    print(f"\n  Written: {to_utxo_path}")
    if output_change_utxo:
        Path(change_utxo_path).write_text(json.dumps(output_change_utxo, indent=2))
        print(f"  Written: {change_utxo_path}")

    # git rm input UTXOs
    print()
    for txid in input_txids:
        r = subprocess.run(['git', 'rm', f'utxo/{txid}.json'], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  git rm: utxo/{txid}.json")
        else:
            print(f"  WARNING: git rm failed for utxo/{txid}.json: {r.stderr.strip()}")

    # Write multi-line commit message to .git/ (not tracked)
    commit_title = f"tx: {from_user} \u2192 {to_user} {amount} GTC"
    commit_msg = commit_title + "\n\n" + "\n".join(pr_body_lines)
    msg_file = Path(".git/GITCOIN_TX_MSG")
    msg_file.write_text(commit_msg, encoding="utf-8")
    print(f"  Written: .git/GITCOIN_TX_MSG  (commit message with TX body)")

    print()
    print("Now run:")
    print(f"  git add utxo/")
    print(f'  git commit -F .git/GITCOIN_TX_MSG')
    print(f"  git push")

    # Auto-create PR using GH_TOKEN if available
    auto_pr = input("\nAuto commit, push and create PR? (requires GH_TOKEN or GH_TOKEN.txt) [Y/n]: ").strip().lower()
    if auto_pr in ('', 'y', 'yes'):
        _auto_commit_push_pr(from_user, to_user, amount, pr_body_lines)


def _auto_commit_push_pr(from_user: str, to_user: str, amount: int, pr_body_lines: list):
    import os, re

    # --- Resolve token ---
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        token_file = Path('GH_TOKEN.txt')
        if token_file.exists():
            token = token_file.read_text(encoding='utf-8').strip()
    if not token:
        print("\nERROR: GitHub token not found.")
        print("Create GH_TOKEN.txt in the repo root with your PAT:")
        print("  echo ghp_yourtoken > GH_TOKEN.txt")
        print("(GH_TOKEN.txt is in .gitignore and will never be committed)")
        return

    # --- Branch name ---
    suggested = f"tx-{from_user[:8]}"
    branch_input = input(f"Branch name [{suggested}]: ").strip()
    branch = branch_input if branch_input else suggested

    # --- git add ---
    print(f"\n  git add utxo/")
    r = subprocess.run(['git', 'add', 'utxo/'], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERROR: git add failed: {r.stderr.strip()}")
        return

    # --- git checkout -b <branch> (create new branch) ---
    current = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                             capture_output=True, text=True).stdout.strip()
    if current != branch:
        print(f"  git switch -c {branch}")
        r = subprocess.run(['git', 'switch', '-c', branch], capture_output=True, text=True)
        if r.returncode != 0:
            # branch may already exist
            r = subprocess.run(['git', 'switch', branch], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"ERROR: git switch failed: {r.stderr.strip()}")
                return

    # --- git commit ---
    msg_file = Path('.git/GITCOIN_TX_MSG')
    print(f"  git commit -F .git/GITCOIN_TX_MSG")
    r = subprocess.run(['git', 'commit', '-F', str(msg_file)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERROR: git commit failed: {r.stderr.strip()}")
        return
    print(f"  {r.stdout.strip()}")

    # --- git push ---
    print(f"  git push origin {branch}")
    r = subprocess.run(['git', 'push', 'origin', branch], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERROR: git push failed: {r.stderr.strip()}")
        return

    # --- Determine head ref for fork support ---
    r = subprocess.run(['git', 'remote', 'get-url', 'origin'], capture_output=True, text=True)
    origin_url = r.stdout.strip()
    m = re.search(r'github\.com[:/](.+?)(?:\.git)?$', origin_url)
    owner = m.group(1).split('/')[0] if m else None
    upstream_repo = 'gitledger/gitcoin'
    head_ref = f"{owner}:{branch}" if owner and owner != 'volta2030' else branch

    # --- gh pr create ---
    title = f"tx: {from_user} \u2192 {to_user} {amount} GTC"
    body = '\n'.join(pr_body_lines)
    env = {**os.environ, 'GH_TOKEN': token}

    print(f"\n  gh pr create ...")
    result = subprocess.run([
        'gh', 'pr', 'create',
        '--repo', upstream_repo,
        '--title', title,
        '--body', body,
        '--base', 'main',
        '--head', head_ref,
    ], capture_output=True, text=True, env=env)

    if result.returncode == 0:
        print(f"\nPR created: {result.stdout.strip()}")
    else:
        err = result.stderr.strip()
        if 'already exists' in err:
            print(f"\nPR already exists for branch '{branch}'.")
        else:
            print(f"\nERROR creating PR: {err}")
            print("Open manually at: https://github.com/gitledger/gitcoin/compare")


if __name__ == '__main__':
    main()
