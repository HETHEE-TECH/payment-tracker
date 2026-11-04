from datetime import date as Date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["pending", "paid", "delayed", "cancelled"]


class PaymentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    date: Date


class PaymentOut(BaseModel):
    id: int
    name: str
    amount: float
    date: str
    status: Status
    new_date: Optional[str] = None
    has_image: bool = False


class StatusUpdate(BaseModel):
    status: Status


class ImportRequest(BaseModel):
    text: str
    replace: bool = False