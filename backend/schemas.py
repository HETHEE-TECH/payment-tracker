from datetime import date as Date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["pending", "paid", "delayed", "cancelled"]


class PaymentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    date: Date


class PaymentAdd(PaymentCreate):
    """Add one payment: the status and the balance amounts can be chosen right away."""
    status: Literal["pending", "paid", "cancelled"] = "pending"
    delayed: bool = False  # Delayed can be on together with Paid or Cancel
    awaiting_amount: Optional[float] = Field(default=None, gt=0)
    received_amount: Optional[float] = Field(default=None, gt=0)
    we_paid_amount: Optional[float] = Field(default=None, gt=0)
    excess_amount: Optional[float] = Field(default=None, gt=0)
    part_pending_amount: Optional[float] = Field(default=None, gt=0)


class PaymentOut(BaseModel):
    id: int
    name: str
    amount: float
    date: str
    status: Status
    new_date: Optional[str] = None
    description: Optional[str] = None
    awaiting_amount: Optional[float] = None  # Awaiting income: still to come in
    we_paid_amount: Optional[float] = None  # We paid
    part_pending_amount: Optional[float] = None  # Part paid pending
    received_amount: Optional[float] = None  # Awaiting income received
    excess_amount: Optional[float] = None  # Excess paid
    category: Optional[str] = None
    has_image: bool = False
    images: List[int] = []  # ids of the payment's images / PDFs, oldest first
    image_types: List[str] = []  # the content type of each id above ("image/png", "application/pdf", ...)
    files_updated: List[str] = []  # stored Excel files changed by this action
    sync_error: Optional[str] = None


class DescriptionUpdate(BaseModel):
    description: str = Field(default="", max_length=500)  # empty clears it


class CategoryUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=60)  # empty clears it


class AmountUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)  # empty clears it


class StatusUpdate(BaseModel):
    status: Optional[Status] = None  # pending, paid or cancelled (an older page may still send delayed)
    delayed: Optional[bool] = None  # Delayed on or off: it can be on together with Paid or Cancel


class ImportRequest(BaseModel):
    text: str
    replace: bool = False