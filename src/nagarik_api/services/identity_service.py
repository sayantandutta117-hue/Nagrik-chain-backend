from sqlalchemy.orm import Session

from ..models import Citizen, ProfessionalCredential
from ..schemas import CitizenCreate, ProfessionalCreate
from ..security import generate_keypair
from .did import citizen_chin, did_for


def create_citizen(db: Session, payload: CitizenCreate) -> Citizen:
    _, public_key = generate_keypair()
    chin = citizen_chin(payload.full_name, payload.date_of_birth, public_key)
    citizen = Citizen(
        chin=chin,
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        did=did_for("chin", chin),
        public_key_pem=public_key,
    )
    db.add(citizen)
    db.commit()
    db.refresh(citizen)
    return citizen


def create_professional_credential(db: Session, payload: ProfessionalCreate) -> ProfessionalCredential:
    pdid = did_for("professional", payload.license_number)
    credential = ProfessionalCredential(
        citizen_id=payload.citizen_id,
        profession=payload.profession,
        license_number=payload.license_number,
        issuer=payload.issuer,
        did=pdid,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return credential
