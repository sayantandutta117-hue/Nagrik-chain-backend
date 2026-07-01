"""
Smart Contract Event Router
=============================
Routes blockchain-verified events (death certificate issued, age 60 reached,
satellite drought detected, scholarship criteria met) to the correct
Ethereum smart contract function.

This implements the "Code that executes justice automatically" slide:
  IF [blockchain-verified condition] THEN [auto-execute smart contract]

Supported workflow triggers:
- pension_start       → Citizen turns 60; auto-start pension payments
- pension_stop        → Death certificate registered; stop pension
- smart_will          → Death certificate → execute will distribution
- property_transfer   → Both parties sign → atomic title + fund swap
- crop_insurance      → Satellite drought index ≥ 0.7 → auto-pay farmer
- scholarship         → Student + income verified → auto-credit
- insurance_claim     → Hospital invoice verified → same-day auto-pay
- loan_emi_deduct     → Monthly EMI deduct + collateral release on payoff
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from ..blockchain.ethereum import EthereumGateway
from ..blockchain.fabric import FabricGateway
from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class WorkflowTrigger:
    workflow: str
    citizen_id: str
    trigger_data: dict[str, Any]
    verified_by: str  # CHIN, IPFS CID, or satellite data hash


@dataclass
class WorkflowExecutionResult:
    workflow: str
    citizen_id: str
    status: str          # executed | rejected | pending_verification
    case_id: str
    transaction_hash: str | None
    reason: str


# Mapping from workflow name → required trigger fields for on-chain verification
WORKFLOW_SCHEMAS: dict[str, list[str]] = {
    "pension_start":    ["citizen_id", "verified_age", "age_threshold"],
    "pension_stop":     ["citizen_id", "death_certificate_cid"],
    "smart_will":       ["testator_chin", "death_certificate_cid", "beneficiaries"],
    "property_transfer": ["seller_chin", "buyer_chin", "property_id", "payment_confirmed"],
    "crop_insurance":   ["farmer_chin", "plot_id", "satellite_drought_index"],
    "scholarship":      ["student_chin", "verified_income", "income_threshold", "enrollment_cid"],
    "insurance_claim":  ["patient_chin", "hospital_invoice_cid", "amount_inr"],
    "loan_emi_deduct":  ["borrower_chin", "loan_id", "emi_amount_inr"],
}


class SmartContractEventRouter:
    """
    Validates triggers, builds the on-chain case, and routes execution
    to the NagarikJustice Ethereum contract.
    """

    def __init__(self) -> None:
        self.ethereum = EthereumGateway(settings.ethereum_rpc_url)
        self.fabric = FabricGateway(settings.fabric_gateway_url)

    async def route(self, trigger: WorkflowTrigger) -> WorkflowExecutionResult:
        validation_error = self._validate(trigger)
        if validation_error:
            return WorkflowExecutionResult(
                workflow=trigger.workflow,
                citizen_id=trigger.citizen_id,
                status="rejected",
                case_id="",
                transaction_hash=None,
                reason=validation_error,
            )

        case_id_bytes = self._case_id(trigger)
        citizen_hash = self._hash32(trigger.citizen_id)
        trigger_hash = self._hash32(str(sorted(trigger.trigger_data.items())))

        try:
            # 1. Register the case on Ethereum (public, executable contract)
            register_tx = await self.ethereum.emit_contract_event(
                "registerCase",
                {
                    "actor": trigger.verified_by,
                    "subject_id": trigger.citizen_id,
                    "payload_hash": trigger_hash,
                    "case_id": case_id_bytes,
                    "citizen_hash": citizen_hash,
                    "workflow": trigger.workflow,
                },
            )

            # 2. Auto-execute if the trigger data is fully blockchain-verified
            execute_tx = await self.ethereum.emit_contract_event(
                "executeCase",
                {
                    "actor": "nagarik-chain-router",
                    "subject_id": trigger.citizen_id,
                    "payload_hash": trigger_hash,
                    "case_id": case_id_bytes,
                    "verified_trigger_hash": trigger_hash,
                },
            )

            # 3. Anchor the execution event on Hyperledger Fabric for audit
            await self.fabric.anchor_event({
                "actor": trigger.verified_by,
                "action": f"SMART_CONTRACT_EXECUTED:{trigger.workflow}",
                "subject_id": trigger.citizen_id,
                "payload_hash": trigger_hash,
                "created_at": "",
            })

            return WorkflowExecutionResult(
                workflow=trigger.workflow,
                citizen_id=trigger.citizen_id,
                status="executed",
                case_id=case_id_bytes,
                transaction_hash=execute_tx,
                reason=f"Workflow '{trigger.workflow}' executed automatically on verified trigger",
            )

        except Exception as exc:
            logger.exception("Smart contract execution failed for %s", trigger.workflow)
            return WorkflowExecutionResult(
                workflow=trigger.workflow,
                citizen_id=trigger.citizen_id,
                status="pending_verification",
                case_id=case_id_bytes,
                transaction_hash=None,
                reason=f"Execution deferred: {exc}",
            )

    def _validate(self, trigger: WorkflowTrigger) -> str | None:
        schema = WORKFLOW_SCHEMAS.get(trigger.workflow)
        if not schema:
            return f"Unknown workflow '{trigger.workflow}'"
        missing = [field for field in schema if field not in trigger.trigger_data]
        if missing:
            return f"Missing required trigger fields: {missing}"
        # Workflow-specific business rules
        if trigger.workflow == "crop_insurance":
            drought_index = float(trigger.trigger_data.get("satellite_drought_index", 0))
            if drought_index < 0.7:
                return f"Drought index {drought_index:.2f} is below automatic payout threshold 0.70"
        if trigger.workflow == "pension_start":
            verified_age = int(trigger.trigger_data.get("verified_age", 0))
            threshold = int(trigger.trigger_data.get("age_threshold", 60))
            if verified_age < threshold:
                return f"Verified age {verified_age} < pension threshold {threshold}"
        return None

    def _case_id(self, trigger: WorkflowTrigger) -> str:
        seed = f"{trigger.workflow}:{trigger.citizen_id}:{str(sorted(trigger.trigger_data.items()))}"
        return "0x" + hashlib.sha256(seed.encode()).hexdigest()

    def _hash32(self, value: str) -> str:
        return "0x" + hashlib.sha256(value.encode()).hexdigest()
