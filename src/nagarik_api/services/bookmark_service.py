"""
Saved Schemes / Bookmark Service
================================
Allows an authenticated citizen to bookmark schemes for later reference.

Bookmarks are owned by the citizen, reference an existing scheme, and are
enforced unique by both an application-level check and a database unique
constraint. The service never stores personal information beyond the
citizen identifier and the scheme identifier.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import SavedScheme, SavedSchemeError
from ..schemas import SavedSchemeCreate, SavedSchemeOut
from .scheme_registry import is_supported_scheme


class BookmarkService:
    def __init__(self) -> None:
        pass

    def save(self, db: Session, citizen_id: str, payload: SavedSchemeCreate) -> SavedSchemeOut:
        if not is_supported_scheme(payload.scheme_id):
            raise SavedSchemeError(
                f"Scheme '{payload.scheme_id}' is not registered",
                status_code=404,
            )
        existing = (
            db.query(SavedScheme)
            .filter_by(citizen_id=citizen_id, scheme_id=payload.scheme_id)
            .first()
        )
        if existing is not None:
            raise SavedSchemeError(
                f"Scheme '{payload.scheme_id}' is already bookmarked",
                status_code=409,
            )

        bookmark = SavedScheme(citizen_id=citizen_id, scheme_id=payload.scheme_id)
        db.add(bookmark)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise SavedSchemeError(
                f"Scheme '{payload.scheme_id}' is already bookmarked",
                status_code=409,
            )
        db.refresh(bookmark)
        return SavedSchemeOut.model_validate(bookmark)

    def list_bookmarks(self, db: Session, citizen_id: str) -> list[SavedSchemeOut]:
        bookmarks = (
            db.query(SavedScheme)
            .filter_by(citizen_id=citizen_id)
            .order_by(SavedScheme.created_at.desc())
            .all()
        )
        return [SavedSchemeOut.model_validate(bookmark) for bookmark in bookmarks]

    def delete(self, db: Session, citizen_id: str, scheme_id: str) -> bool:
        bookmark = (
            db.query(SavedScheme)
            .filter_by(citizen_id=citizen_id, scheme_id=scheme_id)
            .first()
        )
        if bookmark is None:
            raise SavedSchemeError(
                f"Saved scheme '{scheme_id}' was not found",
                status_code=404,
            )
        db.delete(bookmark)
        db.commit()
        return True