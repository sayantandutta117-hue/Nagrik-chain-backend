from datetime import datetime, timezone


def citizen_chin(full_name: str, date_of_birth: str, public_key_pem: str) -> str:
    import hashlib

    seed = f"{full_name}|{date_of_birth}|{public_key_pem}".encode()
    return "CHIN-" + hashlib.sha256(seed).hexdigest()[:18].upper()


def did_for(kind: str, identifier: str) -> str:
    return f"did:nagarik:{kind}:{identifier.lower()}"


def did_document(did: str, public_key_pem: str) -> dict:
    key_id = f"{did}#keys-1"
    return {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": did,
        "created": datetime.now(timezone.utc).isoformat(),
        "verificationMethod": [
            {
                "id": key_id,
                "type": "RsaVerificationKey2018",
                "controller": did,
                "publicKeyPem": public_key_pem,
            }
        ],
        "authentication": [key_id],
        "assertionMethod": [key_id],
    }
