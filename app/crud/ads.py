from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List

from app.models.ad import Ad
from app.schemas.ad import AdCreate


def get_active_ads(db: Session) -> List[Ad]:
    return db.query(Ad).filter(Ad.active == True).all()


def create_ad(db: Session, ad: AdCreate) -> Ad:
    db_ad = Ad(**ad.model_dump())
    db.add(db_ad)
    db.commit()
    db.refresh(db_ad)
    return db_ad