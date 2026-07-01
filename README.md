# Nagarik Chain Backend

India's Blockchain-Based National Digital Identity & Governance System.

> "One Identity. One Lifetime. Zero Paperwork. Zero Corruption."

---

## Architecture Overview

```
Citizen / Officer App
        │
        ▼
   FastAPI (port 8000)
        │
   ┌────┴─────────────────────────────────────────┐
   │              Service Layer                    │
   │  IdentityService  DocumentService             │
   │  EligibilityEngine  FraudDetector             │
   │  IntegrityScoreEngine  WhistleblowerVault     │
   │  DecoyApplicationSystem  SmartContractRouter  │
   └────┬───────────┬──────────────┬──────────────┘
        │           │              │
   AI Layer    Blockchain      Database
   ┌──────┐   ┌──────────┐   ┌──────────┐
   │DeepFace│  │HLFabric  │   │SQLite/   │
   │DocAI  │  │Ethereum  │   │Postgres  │
   │FraudML│  │IPFS Kubo │   └──────────┘
   │ZK-SNARKs  └──────────┘
   └──────┘
```

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI 0.111+, Pydantic v2, Uvicorn |
| Database | SQLAlchemy 2.0 (SQLite dev / Postgres prod) |
| Blockchain | Hyperledger Fabric (audit anchoring), Ethereum/Solidity (smart contracts), IPFS/Kubo (document storage) |
| AI/ML | DeepFace (biometrics), XGBoost/LightGBM (fraud), Hugging Face Transformers (document AI), SHAP (explainability) |
| Biometrics | DeepFace (FaceNet512), SourceAFIS-compatible fingerprint |
| Privacy | ZK-SNARK commitments (BN128/Pedersen), Fernet encryption |
| Task Queue | Celery + Redis |
| Containers | Docker + Kubernetes |
| Security | RSA-3072 keypairs, HMAC-SHA256, Fernet (AES-128-CBC) |

## Quick Start (Local Dev)

```bash
# Install core dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Start API (SQLite, no blockchain required for dev)
uvicorn nagarik_api.main:app --reload
# → http://127.0.0.1:8000/docs
```

## Full Stack (Docker)

```bash
docker compose up --build
# API: http://localhost:8000/docs
# Ganache Ethereum: http://localhost:8545
# IPFS Gateway: http://localhost:8080
```

## Install AI Extras

```bash
pip install -e ".[ai]"
# Enables: DeepFace, XGBoost, LightGBM, Hugging Face, Tesseract OCR
```

## Train Fraud Detection Model

```bash
# First build a training CSV matching src/nagarik_api/ai/fraud_dataset_schema.json
python -m nagarik_api.ai.train_fraud_model \
  --dataset fraud_training.csv \
  --out-dir models/fraud \
  --backend xgboost

# Set env vars to load the trained model
export FRAUD_MODEL_DIR=models/fraud
export FRAUD_MODEL_VERSION=latest
```

## Generate Biometric Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → Set as BIOMETRIC_EMBEDDING_KEY in .env
```

## Deploy Ethereum Contracts

```bash
# 1. Start Ganache (already in docker-compose)
# 2. Compile NagarikJustice.sol with solc or Hardhat
# 3. Deploy:
python -c "
from nagarik_api.blockchain.ethereum import EthereumGateway
gw = EthereumGateway('http://localhost:8545')
addr = gw.deploy_contract('NagarikJustice.abi', 'NagarikJustice.bin')
print('Contract deployed at:', addr)
"
# 4. Set ETHEREUM_AUDIT_CONTRACT_ADDRESS=<addr> in .env
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /citizens | Issue CHIN + W3C DID |
| POST | /professionals | Issue P-DID professional credential |
| POST | /documents | Notarize document (IPFS + AI classification) |
| POST | /eligibility | Evaluate scheme eligibility (scholarship/pension/crop) |
| POST | /fraud/report | Anonymous encrypted bribe report |
| POST | /fraud/score | ML-based fraud risk scoring with SHAP |
| POST | /biometrics/enroll | Face enrollment with liveness check |
| POST | /biometrics/verify | Face verification against enrolled embedding |
| POST | /zk/prove | Generate ZK-SNARK commitment (age/income/license) |
| POST | /zk/verify | Verify a ZK commitment |
| POST | /workflows/trigger | Route smart contract execution (pension/crop/etc) |
| POST | /officers/integrity/update | Update officer integrity signals |
| GET | /officers/{id}/integrity | Get officer integrity score + flags |
| POST | /vault/seal | Seal whistleblower report (dead-man-switch) |
| GET | /vault/status/{hash} | Check report escalation status |
| POST | /decoy/seed | Seed a decoy application for an officer |
| POST | /decoy/outcome | Record decoy pass/fail |
| GET | /audit/{subject_id} | Retrieve blockchain-anchored audit trail |

## Supported Smart Contract Workflows

| Workflow | Trigger | Execution |
|----------|---------|-----------|
| `pension_start` | Citizen age ≥ 60 (verified) | Auto-start monthly pension |
| `pension_stop` | Death certificate registered | Stop pension payments |
| `smart_will` | Verified death certificate | Execute will distribution |
| `property_transfer` | Both parties signed + payment confirmed | Atomic title + fund swap |
| `crop_insurance` | Satellite drought index ≥ 0.7 | Auto-pay farmer same day |
| `scholarship` | Student verified + income ≤ threshold | Auto-credit scholarship |
| `insurance_claim` | Hospital invoice verified | Same-day auto-pay to hospital |
| `loan_emi_deduct` | Monthly cycle | Auto-deduct EMI, release collateral on payoff |

## Anti-Corruption Features

- **Bribe Report Button** — Anonymous, encrypted, routes via `/fraud/report`
- **AI Pattern Detector** — Rejection-rate spike detection in `IntegrityScoreEngine`
- **Decoy Applications** — Officers cannot identify real vs decoy; failure recorded automatically
- **Integrity Score** — Live 0-100 score computed from rejection spike + delay + bribe reports + decoy failure + overturn rate
- **Whistleblower Vault** — Fernet-encrypted, dead-man-switch auto-escalation to court
- **On-Chain Audit** — Every action anchored on Hyperledger Fabric + Ethereum

## Environment Variables

See `.env.example` for all configuration options including:
- `DATABASE_URL`, `API_KEY`
- Fabric: `FABRIC_GATEWAY_URL`, `FABRIC_MSP_ID`, `FABRIC_CHANNEL_NAME`, `FABRIC_TLS_CERT_PATH`, ...
- Ethereum: `ETHEREUM_RPC_URL`, `ETHEREUM_PRIVATE_KEY`, `ETHEREUM_AUDIT_CONTRACT_ADDRESS`, ...
- IPFS: `IPFS_API_URL`
- Biometrics: `BIOMETRIC_EMBEDDING_KEY`, `FACE_EMBEDDING_MODEL`, `FACE_SIMILARITY_THRESHOLD`
- Document AI: `DOCUMENT_AI_OCR_BACKEND`, `DOCUMENT_AI_CLASSIFIER_MODEL`, ...
- Fraud ML: `FRAUD_MODEL_DIR`, `FRAUD_MODEL_VERSION`
- Vault: `COURT_VAULT_KEY`
- Decoy: `DECOY_SEED_KEY`
- Celery: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`

## Run Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Kubernetes Deployment

```bash
# Build and push image
docker build -t nagarik-chain-backend:0.1.0 .
docker push <your-registry>/nagarik-chain-backend:0.1.0

# Deploy
kubectl apply -f k8s/deployment.yaml
```
## Contributors

- Backend Developer: Sayantan
- Project Type: Full-Stack Blockchain + AI Governance System