from datetime import date as Date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["pending", "paid", "delayed", "cancelled", "need_to_come"]


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
    description: Optional[str] = None
    has_image: bool = False
    images: List[int] = []  # ids of the payment's images, oldest first
    files_updated: List[str] = []  # stored Excel files changed by this action
    sync_error: Optional[str] = None


class DescriptionUpdate(BaseModel):
    description: str = Field(default="", max_length=500)  # empty clears it


class StatusUpdate(BaseModel):
    status: Status


class ImportRequest(BaseModel):
    text: str
    replace: bool = False