from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


class FabricError(RuntimeError):
    pass


class FabricGateway:
    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url
        self.msp_id = os.getenv("FABRIC_MSP_ID", "")
        self.channel_name = os.getenv("FABRIC_CHANNEL_NAME", "")
        self.chaincode_name = os.getenv("FABRIC_CHAINCODE_NAME", "nagarik")
        self.audit_function = os.getenv("FABRIC_AUDIT_FUNCTION", "RecordAudit")
        self.tls_cert_path = os.getenv("FABRIC_TLS_CERT_PATH", "")
        self.identity_cert_path = os.getenv("FABRIC_IDENTITY_CERT_PATH", "")
        self.private_key_path = os.getenv("FABRIC_PRIVATE_KEY_PATH", "")

    async def anchor_event(self, event: dict) -> str:
        event_id = hashlib.sha256(
            f"{event['actor']}|{event['action']}|{event['subject_id']}|{event['payload_hash']}".encode()
        ).hexdigest()
        result = self.invoke_chaincode(
            self.audit_function,
            event_id,
            event["actor"],
            event["action"],
            event["subject_id"],
            event["payload_hash"],
            event.get("created_at", ""),
        )
        tx_id = result.decode("utf-8") if isinstance(result, bytes) and result else event_id
        return tx_id

    def invoke_chaincode(self, function_name: str, *args: str) -> bytes:
        return self._contract().submit_transaction(function_name, *args)

    def evaluate_chaincode(self, function_name: str, *args: str) -> bytes:
        return self._contract().evaluate_transaction(function_name, *args)

    def enroll_identity(
        self,
        ca_url: str,
        enrollment_id: str,
        enrollment_secret: str,
        msp_id: str,
        wallet_dir: str,
    ) -> dict:
        try:
            from hfc.fabric_ca.caservice import CAClient
        except ImportError as exc:
            raise FabricError("Missing dependency 'fabric-sdk-py' for Fabric CA enrollment.") from exc

        ca_client = CAClient(target=ca_url)
        enrollment = ca_client.enroll(enrollment_id, enrollment_secret)
        wallet_path = Path(wallet_dir)
        wallet_path.mkdir(parents=True, exist_ok=True)
        cert_path = wallet_path / f"{enrollment_id}-cert.pem"
        key_path = wallet_path / f"{enrollment_id}-key.pem"
        cert_path.write_text(enrollment.cert, encoding="utf-8")
        key_path.write_text(enrollment.private_key, encoding="utf-8")
        return {
            "msp_id": msp_id,
            "certificate_path": str(cert_path),
            "private_key_path": str(key_path),
        }

    def _contract(self) -> Any:
        self._require_config()
        gateway = self._gateway()
        network = gateway.get_network(self.channel_name)
        return network.get_contract(self.chaincode_name)

    def _gateway(self) -> Any:
        try:
            import grpc
            from cryptography.hazmat.primitives import serialization
            from fabric_gateway import Gateway, Identity, Signer, connect
        except ImportError as exc:
            raise FabricError("Missing Fabric Gateway SDK dependencies.") from exc

        tls_credentials = grpc.ssl_channel_credentials(Path(self.tls_cert_path).read_bytes())
        channel = grpc.secure_channel(self.gateway_url, tls_credentials)
        certificate = Path(self.identity_cert_path).read_bytes()
        private_key = serialization.load_pem_private_key(
            Path(self.private_key_path).read_bytes(),
            password=None,
        )
        identity = Identity(self.msp_id, certificate)
        signer = Signer(private_key)
        try:
            return connect(identity=identity, signer=signer, client=channel)
        except TypeError:
            return Gateway(channel, identity, signer)

    def _require_config(self) -> None:
        required = {
            "FABRIC_MSP_ID": self.msp_id,
            "FABRIC_CHANNEL_NAME": self.channel_name,
            "FABRIC_TLS_CERT_PATH": self.tls_cert_path,
            "FABRIC_IDENTITY_CERT_PATH": self.identity_cert_path,
            "FABRIC_PRIVATE_KEY_PATH": self.private_key_path,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise FabricError(f"Missing Fabric Gateway configuration: {', '.join(missing)}")
