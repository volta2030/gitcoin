# GitCoin ⛓

A fully decentralized token ecosystem that runs entirely on GitHub.
No servers, no wallets, no gas fees — just forks, pull requests, and consensus.

**Live Balance Explorer**:
- **Root ledger**: https://gitledger.github.io/gitcoin/ — Balances, Transactions, Validators tabs
- **Your fork**: `https://{your-github-username}.github.io/gitcoin/` — available after enabling GitHub Pages in your fork's Settings

---

## How It Works

| Blockchain concept | GitCoin equivalent |
|---|---|
| Full node | Fork of this repository |
| Transaction | Pull Request with UTXO file changes |
| Block | Merge commit on `main` |
| Hash chain | Git commit history |
| Validator / miner | Any GitHub account with a registered public key in `validators/pubkeys.json` |
| Consensus (TX) | Sender posts `/approve` on their own PR |
| Consensus (other) | ⌈1/3⌉ of all validators post `/approve` |
| Double-spend guard | Git merge conflict (two PRs can't delete the same file) |
| UTXO integrity | Merkle inclusion proof (O(log n) per input) verified against `ledger.json` |
| Total supply | 4,294,967,295 GTC (fixed, no inflation) |
| Minimum unit | 1 GTC (integer amounts only) |

### Transaction lifecycle

```
1. Pull the latest main branch
2. Run create_transaction.py
   - Lists your UTXOs with numbered shortcuts
   - Fetches current Merkle root from ledger.json (GitHub Pages)
   - Builds and signs the TX, computes a Merkle inclusion proof per input
   - Writes output UTXO files and git rm's inputs automatically
3. git add utxo/ && git commit && git push to a new branch, open a PR
4. validate-tx.yml:
   a. Verifies Ed25519 signature and UTXO ownership
   b. Verifies Merkle inclusion proof matches the canonical root in ledger.json
   c. Verifies output txids are cryptographically derived from inputs (chain integrity)
5. On success: tx-valid label attached + sender prompted to post /approve
6. Sender comments /approve on the PR
7. PR is ready to merge
8. update-pages.yml rebuilds ledger.json (with new Merkle root) and deploys explorer
```

> Non-TX PRs (key registration, code changes) require ⌈1/3⌉ of all validators to post `/approve` before merging.

---

## Chain Integrity & Merkle Proof

Each TX PR must include:

| Field | Description |
|---|---|
| `MERKLE_ROOT` | SHA-256 Merkle root of the entire UTXO set at PR creation time |
| `MERKLE_PROOF` | JSON map of `txid → proof path` — one inclusion proof per input UTXO |

`create_transaction.py` generates these automatically by fetching `ledger.json` from GitHub Pages.

**Why this matters:**
- The validator only needs to walk O(log n) hashes per input instead of scanning all UTXOs
- If another TX is merged while your PR is open, the root changes and your proof becomes invalid — you must regenerate the TX. This is by design: it prevents double-spending the same UTXO across two concurrent PRs.
- Past transactions are unaffected — already-merged UTXOs on `main` are settled and do not require re-validation.

---

## Requirements

```bash
pip install cryptography
```

You also need:
- A GitHub account
- Git installed locally
- `gh` CLI (optional): https://cli.github.com

---

## Quick Start

### Step 1 — Fork this repository

Click **Fork** at the top of this page. Your fork is your full node — it contains the entire ledger history.

### Step 2 — Clone and set upstream remote

```bash
git clone https://github.com/YOUR_USERNAME/gitcoin
cd gitcoin
git remote add upstream https://github.com/gitledger/gitcoin.git
```

`upstream` is required for the auto-PR feature — it tells the script which repo to open the PR against.

### Step 3 — Generate your Ed25519 identity

```bash
python3 .github/scripts/generate_keypair.py
```

Output:
```
⚠️  PRIVATE KEY — Keep this secret, never commit it:
  <your-private-key-base64url>

✅  PUBLIC KEY — Share this in your REGISTER_KEY PR:
  <your-public-key-base64url>
```

**Store your private key in a password manager.** If you lose it, you lose access to your coins. Never commit it to any repository.

### Step 4 — Register your public key

Before you can send GTC, your public key must be in `validators/pubkeys.json`.

**Create a PR** to this repo's `main` branch with:

**Changed file** — add your entry to `validators/pubkeys.json`:
```json
{
  "existing_user": "their_key",
  "YOUR_GITHUB_USERNAME": "YOUR_PUBLIC_KEY_BASE64URL"
}
```

**PR title**: `register: YOUR_GITHUB_USERNAME`

**PR body**: anything (no `TX_VERSION` needed — the workflow detects this is not a TX and requires ⌈1/3⌉ of all validators to approve).

Once merged, you are automatically added to the validator pool.

---

## Checking Your Balance

Visit the live balance explorer:
```
https://<owner>.github.io/gitcoin/
```

Or inspect the ledger directly:
```bash
git pull origin main
cat docs/ledger.json   # includes merkle_root and utxo_txids
```

---

## Sending GTC

### Step 1 — Pull the latest state

```bash
git pull origin main
```

### Step 2 — Run the transaction builder

```bash
python3 .github/scripts/create_transaction.py
```

The script will:
1. Show your UTXOs as a numbered list — enter `1` or `1,2` instead of full txids
2. Fetch the current Merkle root from GitHub Pages
3. Build and sign the TX, compute inclusion proofs
4. Write output UTXO files and `git rm` input files automatically

### Step 3 — Commit and push

The script writes `.git/GITCOIN_TX_MSG` with the full TX body as the commit message. Use it directly:

```bash
git add utxo/
git commit -F .git/GITCOIN_TX_MSG
git push origin <new-branch-name>
```

### Step 4 — Open a PR (automatic)

After `git push`, the script will ask:

```
Auto-create PR now? (requires GH_TOKEN env var) [Y/n]:
```

- **With token**: PR is created automatically — works from forks too.
- **Without token**: open the PR manually at `https://github.com/gitledger/gitcoin/compare`

To enable auto-PR, create a `GH_TOKEN.txt` file in the repo root with your PAT  
(create a PAT with `repo` scope at https://github.com/settings/tokens):

```bash
echo ghp_yourtoken > GH_TOKEN.txt
```

`GH_TOKEN.txt` is listed in `.gitignore` and will **never be committed**.

### Step 5 — Approve your transaction

`validate-tx.yml` runs automatically. If valid:
- `tx-valid` label is attached
- A comment asks you to post `/approve` on the PR

Simply comment `/approve` on your own PR and it is ready to merge.

> **If another TX merges while your PR is open**, the Merkle root changes and validation will fail with *"Merkle root mismatch"*. Rerun `create_transaction.py` on the latest `main` to regenerate a fresh proof.

---

## Becoming a Validator

Anyone with a registered public key in `validators/pubkeys.json` is a validator.

### How to join

1. Generate your keypair: `python3 .github/scripts/generate_keypair.py`
2. Open a PR adding `"YOUR_USERNAME": "YOUR_PUBLIC_KEY"` to `validators/pubkeys.json`
3. Once merged, you are immediately in the validator pool

### Scoring (optional metadata)

Scores in `validators/registry.json` are metadata only. Anyone not listed defaults to **100 points**.

| Action | Points |
|---|---|
| Comment `/approve` on a non-TX PR | +20 |
| Submitting a valid TX that gets merged | +5 |

Inactivity penalty: **−10 points per week** with no `/approve` activity.

---

## Requirements

```bash
pip install cryptography
```

You also need:
- A GitHub account
- Git installed locally
- `gh` CLI (optional, for convenience): https://cli.github.com

---

## Quick Start

### Step 1 — Fork this repository

Click **Fork** at the top of this page. Your fork is your full node — it contains the entire ledger history.

### Step 2 — Clone and set upstream remote

```bash
git clone https://github.com/YOUR_USERNAME/gitcoin
cd gitcoin
git remote add upstream https://github.com/gitledger/gitcoin.git
```

`upstream` is required for the auto-PR feature — it tells the script which repo to open the PR against.

### Step 3 — Generate your Ed25519 identity

```bash
python3 .github/scripts/generate_keypair.py
```

Output:
```
⚠️  PRIVATE KEY — Keep this secret, never commit it:
  <your-private-key-base64url>

✅  PUBLIC KEY — Share this in your REGISTER_KEY PR:
  <your-public-key-base64url>
```

**Store your private key in a password manager.** If you lose it, you lose access to your coins. Never commit it to any repository.

### Step 4 — Register your public key

Before you can send GTC, your public key must be in `validators/pubkeys.json`.

**Create a PR** to this repo's `main` branch with:

**Changed file** — add your entry to `validators/pubkeys.json`:
```json
{
  "existing_user": "their_key",
  "YOUR_GITHUB_USERNAME": "YOUR_PUBLIC_KEY_BASE64URL"
}
```

**PR title**: `register: YOUR_GITHUB_USERNAME`

**PR body**: anything (no `TX_VERSION` needed — the workflow detects this is not a TX and requires ⌈1/3⌉ of all validators to approve).

Once a maintainer merges the PR, you can start transacting — and you are automatically added to the validator pool.

---

## Checking Your Balance

Visit the live balance explorer:
```
https://<owner>.github.io/gitcoin/
```

Or inspect the ledger directly:
```bash
git pull origin main
cat docs/ledger.json
```

Or scan your UTXOs manually:
```bash
grep -rl '"owner": "YOUR_USERNAME"' utxo/
```

---

## Sending GTC

### Step 1 — Pull the latest state

```bash
git pull origin main
```

### Step 2 — Find your UTXOs

```bash
grep -rl '"owner": "YOUR_USERNAME"' utxo/
```

Note the txid values (the filenames without `.json`).

### Step 3 — Run the transaction builder

```bash
python3 .github/scripts/create_transaction.py
```

You will be prompted for:
- Your GitHub username
- Your private key
- Recipient username
- Amount to send (GTC)
- UTXO txids to spend (comma-separated)
- Optional memo

The script outputs the exact PR body to copy and the file changes to make. It can also write the output UTXO files automatically.

### Step 4 — Commit and push the changes

The script writes `.git/GITCOIN_TX_MSG` with the full TX body as the commit message:

```bash
git add utxo/
git commit -F .git/GITCOIN_TX_MSG
git push origin <new-branch-name>
```

### Step 5 — Open a PR (automatic)

After `git push`, the script asks:

```
Auto-create PR now? (requires GH_TOKEN env var) [Y/n]:
```

- **With token**: PR is created automatically — works from forks too.
- **Without token**: open manually at `https://github.com/gitledger/gitcoin/compare`

To enable auto-PR, create a `GH_TOKEN.txt` file in the repo root with your PAT  
(create a PAT with `repo` scope at https://github.com/settings/tokens):

```bash
echo ghp_yourtoken > GH_TOKEN.txt
```

`GH_TOKEN.txt` is listed in `.gitignore` and will **never be committed**.

### Step 6 — Wait for consensus

`validate-tx.yml` runs automatically. If valid:
- `tx-valid` label is attached
- A **Validator Vote Requested** comment is posted listing selected validators and deadline
- Selected validators comment `/approve` — anyone in `validators/pubkeys.json` is eligible
- When ⌈2/3⌉ approvals are reached, merge the PR

---

## Becoming a Validator

Anyone with a registered public key in `validators/pubkeys.json` is a validator.

### How to join

1. Generate your keypair: `python3 .github/scripts/generate_keypair.py`
2. Open a PR adding `"YOUR_USERNAME": "YOUR_PUBLIC_KEY"` to `validators/pubkeys.json`
3. Once merged, you are immediately in the validator pool

### Scoring (optional metadata)

Scores in `validators/registry.json` are metadata only. Anyone not listed defaults to **100 points**.

| Action | Points |
|---|---|
| Comment `/approve` on a non-TX PR | +20 |
| Submitting a valid TX that gets merged | +5 |

Inactivity penalty: **−10 points per week** with no `/approve` activity.

### How to vote on non-TX PRs

When a code change or key registration PR is opened:

1. You receive a GitHub @mention in the review comment.
2. Review the PR changes.
3. Comment exactly `/approve` to cast your vote.

⌈1/3⌉ of all registered validators must approve before the PR can be merged.

---

## Transaction Reference

### Transfer (TX_VERSION: 1)

```
TX_VERSION: 1
FROM: alice
TO: bob
AMOUNT: 50
INPUT_TXIDS: a1b2c3d4e5f6...,d4e5f6a1b2c3...
OUTPUT_TO_TXID: f7a8b9c0d1e2...
OUTPUT_CHANGE_TXID: e3d4c5b6a7f8...
MEMO: payment for work
SIGNATURE: <base64url Ed25519 signature>
```

| Field | Required | Description |
|---|---|---|
| `TX_VERSION` | ✅ | Must be `1` |
| `FROM` | ✅ | Must match PR author's GitHub login |
| `TO` | ✅ | Recipient GitHub username |
| `AMOUNT` | ✅ | Integer GTC to send (minimum: 1 GTC) |
| `INPUT_TXIDS` | ✅ | Comma-separated txids of UTXOs you are spending |
| `OUTPUT_TO_TXID` | ✅ | txid of new UTXO file added for the recipient |
| `OUTPUT_CHANGE_TXID` | if change | txid of change UTXO returned to you |
| `MEMO` | optional | Free-text note |
| `SIGNATURE` | ✅ | Ed25519 signature over the canonical message |

**Conservation rule**: `sum(inputs) == AMOUNT + change`. No GTC can be created or destroyed.

> **Note**: All amounts are integers. The minimum transactable unit is **1 GTC**. Decimal amounts are not supported.

### Key Registration (TX_VERSION: REGISTER_KEY)

```
TX_VERSION: REGISTER_KEY
USERNAME: alice
PUBLIC_KEY: <base64url Ed25519 public key>
```

PR must only modify `validators/pubkeys.json` by adding one new entry.

---

## UTXO File Format

Each file in `utxo/` represents one unspent coin:

```json
{
  "txid": "a1b2c3d4...",
  "owner": "alice",
  "amount": 100,
  "unit": "GTC",
  "created_at_block": "<merge commit SHA>",
  "created_at_height": 42
}
```

The filename must match the `txid` field: `utxo/<txid>.json`.

**Computing a txid**:
```python
import hashlib
txid = hashlib.sha256(f"{owner}{amount}{created_at_block}".encode()).hexdigest()
```

---

## Founder / Repository Setup Guide

Follow these steps once when deploying a new GitCoin instance.

### 1. Create the repository

Create a new public GitHub repository. Do **not** enable Branch Protection yet.

### 2. Generate your keypair

```bash
python3 .github/scripts/generate_keypair.py
```

### 3. Compute your genesis txid

```python
import hashlib
owner = "YOUR_GITHUB_USERNAME"
amount = 4294967295
block_hash = "0" * 64
txid = hashlib.sha256(f"{owner}{amount}{block_hash}".encode()).hexdigest()
print(txid)
```

### 4. Edit the genesis files

**`genesis/genesis.json`** — replace `REPLACE_WITH_FOUNDER_USERNAME` and `REPLACE_WITH_GENESIS_TXID`.

**`validators/pubkeys.json`** — replace placeholders with your username and public key.

**`validators/registry.json`** — replace `REPLACE_WITH_FOUNDER_USERNAME` with your username.

**Create `utxo/<genesis_txid>.json`**:
```json
{
  "txid": "<your computed genesis txid>",
  "owner": "YOUR_GITHUB_USERNAME",
  "amount": 4294967295,
  "unit": "GTC",
  "created_at_block": "0000000000000000000000000000000000000000000000000000000000000000",
  "created_at_height": 0
}
```

### 5. Commit everything to main

```bash
git add .
git commit -m "genesis: initialize GitCoin ledger"
git push origin main
```

### 6. Enable GitHub Pages

In your repository **Settings → Pages**:
- Set **Source** to **GitHub Actions**

### 7. Configure Branch Protection (do this last)

In **Settings → Branches → Add rule** for `main`:

- [x] **Require status checks to pass before merging**
  - Add required check: `validate-tx / validate`
  - Add required check: `consensus-check / passed`
- [x] **Require branches to be up to date before merging**
- [x] **Include administrators** ← CRITICAL: do not skip this
- [x] **Allow auto-merge**
- [ ] Allow force pushes — leave unchecked
- [ ] Allow deletions — leave unchecked

> After enabling "Include administrators", even you cannot merge without going through consensus. This is intentional — it is the foundation of the system's trustlessness.

### 8. Create required labels

In your repository Issues → Labels, create:
- `tx-valid` (color: `#2ea043`)
- `tx-invalid` (color: `#f85149`)
- `tx-expired` (color: `#8b949e`)

---

## Security Notes

| Property | How it is enforced |
|---|---|
| No stored bot keys | All workflows use only ephemeral `GITHUB_TOKEN` (auto-issued per run, expires on completion) |
| No admin bypass | Branch Protection includes administrators |
| No double-spend | Git merge conflict blocks the second PR deleting the same UTXO file |
| No code injection from PR | `pull_request_target` runs `main` branch code; the PR head branch is never checked out or executed |
| No shell injection from PR body | PR body is parsed as plain text by Python, never interpolated into shell commands |
| Signature forgery | Ed25519 signatures are verified against the registered public key for each sender |
| Sybil validators | Public key registration required; key must be merged into main via consensus |

---

## Architecture Overview

```
.github/
├── workflows/
│   ├── validate-tx.yml        pull_request_target → verify TX sig; on success posts validator
│   │                          vote comment inline (no separate assign-validators trigger needed)
│   │                          non-TX PRs get immediate success on both required checks
│   ├── consensus-check.yml    issue_comment → count /approve from pubkeys.json validators
│   ├── expire-tx.yml          schedule (6h) → close PRs past 48h deadline
│   └── update-pages.yml       push to main (utxo/** or validators/**) → rebuild + deploy Pages
└── scripts/
    ├── validate_tx.py          Core validation logic (TRANSFER + REGISTER_KEY)
    ├── update_ledger.py        UTXO scanner → ledger.json with balances, TX history, validators
    ├── generate_keypair.py     User tool: generate Ed25519 keypair
    └── create_transaction.py   User tool: build, sign, write files, git rm inputs automatically

utxo/                          One JSON file per unspent coin
validators/
    pubkeys.json               Ed25519 public keys per GitHub username (validator pool)
    registry.json              Optional scoring metadata (score, last_active)
genesis/
    genesis.json               Genesis block metadata
docs/
    index.html                 Explorer UI: Balances / Transactions / Validators tabs
    ledger.json                Snapshot rebuilt after every merge to main
```

---

## Portability

If GitHub ever becomes unavailable, the entire ledger history is preserved in every fork's `git log`. The same workflow logic can be migrated to:

- **GitLab** (GitLab CI/CD)
- **Gitea / Forgejo** (Gitea Actions)
- **Radicle** (decentralized git hosting)

The UTXO files and commit history are the canonical truth. No data lives outside the repository.
