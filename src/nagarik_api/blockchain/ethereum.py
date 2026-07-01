from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


class EthereumError(RuntimeError):
    pass


class EthereumGateway:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.web3 = self._connect(rpc_url)
        self.chain_id = int(os.getenv("ETHEREUM_CHAIN_ID", self.web3.eth.chain_id))
        self.private_key = os.getenv("ETHEREUM_PRIVATE_KEY")
        self.from_address = os.getenv("ETHEREUM_FROM_ADDRESS")
        if self.private_key and not self.from_address:
            self.from_address = self.web3.eth.account.from_key(self.private_key).address
        self.contract_address = os.getenv("ETHEREUM_AUDIT_CONTRACT_ADDRESS")
        self.contract_abi_path = os.getenv("ETHEREUM_AUDIT_CONTRACT_ABI")
        self.audit_function = os.getenv("ETHEREUM_AUDIT_FUNCTION", "recordAudit")

    async def emit_contract_event(self, event_name: str, payload: dict) -> str:
        contract = self._audit_contract()
        tx_function = getattr(contract.functions, self.audit_function)(
            str(event_name),
            str(payload["actor"]),
            str(payload["subject_id"]),
            self.web3.to_bytes(hexstr=payload["payload_hash"]),
        )
        tx_hash = self.sign_and_send(tx_function)
        receipt = self.verify_receipt(tx_hash)
        return receipt["transactionHash"].hex()

    def deploy_contract(self, abi_path: str, bytecode_path: str, constructor_args: Iterable[Any] = ()) -> str:
        abi = json.loads(Path(abi_path).read_text(encoding="utf-8"))
        bytecode = Path(bytecode_path).read_text(encoding="utf-8").strip()
        contract = self.web3.eth.contract(abi=abi, bytecode=bytecode)
        tx_hash = self.sign_and_send(contract.constructor(*constructor_args))
        receipt = self.verify_receipt(tx_hash)
        address = receipt.get("contractAddress")
        if not address:
            raise EthereumError("Contract deployment receipt did not include contractAddress")
        return self.web3.to_checksum_address(address)

    def sign_and_send(self, tx_function: Any) -> bytes:
        if not self.private_key or not self.from_address:
            raise EthereumError("ETHEREUM_PRIVATE_KEY and ETHEREUM_FROM_ADDRESS are required")
        nonce = self.web3.eth.get_transaction_count(self.from_address)
        transaction = tx_function.build_transaction(
            {
                "from": self.from_address,
                "nonce": nonce,
                "chainId": self.chain_id,
                "gas": int(os.getenv("ETHEREUM_GAS_LIMIT", "500000")),
                "gasPrice": self.web3.eth.gas_price,
            }
        )
        signed = self.web3.eth.account.sign_transaction(transaction, private_key=self.private_key)
        raw_transaction = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
        return self.web3.eth.send_raw_transaction(raw_transaction)

    def verify_receipt(self, tx_hash: bytes | str, timeout: int | None = None) -> dict:
        receipt = self.web3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=timeout or int(os.getenv("ETHEREUM_RECEIPT_TIMEOUT", "120")),
        )
        if int(receipt.get("status", 0)) != 1:
            raise EthereumError(f"Ethereum transaction failed: {receipt}")
        return dict(receipt)

    def listen_events(self, event_name: str, from_block: int | str = "latest") -> list[dict]:
        contract = self._audit_contract()
        event = getattr(contract.events, event_name)
        event_filter = event.create_filter(fromBlock=from_block)
        return [dict(entry) for entry in event_filter.get_all_entries()]

    def _audit_contract(self) -> Any:
        if not self.contract_address or not self.contract_abi_path:
            raise EthereumError(
                "ETHEREUM_AUDIT_CONTRACT_ADDRESS and ETHEREUM_AUDIT_CONTRACT_ABI are required"
            )
        abi = json.loads(Path(self.contract_abi_path).read_text(encoding="utf-8"))
        return self.web3.eth.contract(
            address=self.web3.to_checksum_address(self.contract_address),
            abi=abi,
        )

    def _connect(self, rpc_url: str) -> Any:
        try:
            from web3 import Web3
        except ImportError as exc:
            raise EthereumError("Missing dependency 'web3'. Install project blockchain extras.") from exc
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not web3.is_connected():
            raise EthereumError(f"Could not connect to Ethereum RPC: {rpc_url}")
        return web3
