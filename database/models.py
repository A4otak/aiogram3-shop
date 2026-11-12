from dataclasses import dataclass
from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class TopupStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"


class PaymentKind(StrEnum):
    ORDER = "order"
    TOPUP = "topup"


class PaymentMethod(StrEnum):
    CRYPTOBOT = "cryptobot"
    BALANCE = "balance"


class BalanceReason(StrEnum):
    ADMIN_GRANT = "admin_grant"
    ADMIN_WITHDRAW = "admin_withdraw"
    PURCHASE = "purchase"
    REFUND = "refund"
    TOPUP = "topup"


class PurchaseStatus(StrEnum):
    SUCCESS = "success"
    REFUNDED = "refunded"
    OUT_OF_STOCK = "out_of_stock"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    ALREADY_PROCESSED = "already_processed"


@dataclass(frozen=True, slots=True)
class User:
    user_id: int
    username: str | None
    full_name: str
    balance: int


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    name: str
    stock: int


@dataclass(frozen=True, slots=True)
class ProductGroup:
    id: int
    category_id: int
    category_name: str
    name: str
    price: int
    stock: int


@dataclass(frozen=True, slots=True)
class Order:
    id: int
    user_id: int
    product_id: int
    price: int
    quantity: int
    method: PaymentMethod
    invoice_id: int | None
    status: OrderStatus
    created_at: str


@dataclass(frozen=True, slots=True)
class Topup:
    id: int
    user_id: int
    amount: int
    invoice_id: int | None
    status: TopupStatus


@dataclass(frozen=True, slots=True)
class PurchaseRecord:
    order_id: int
    product_name: str
    category_name: str
    price: int
    quantity: int
    method: PaymentMethod
    paid_at: str | None


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    kind: PaymentKind
    id: int
    amount: int
    quantity: int
    method: PaymentMethod
    status: OrderStatus
    created_at: str


@dataclass(frozen=True, slots=True)
class ClaimedItem:
    id: int
    name: str
    content: str


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    status: PurchaseStatus
    order_id: int | None = None
    product_name: str | None = None
    contents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Stats:
    users: int
    balances: int
    in_stock: int
    sold_items: int
    orders: int
    revenue: int
    crypto_revenue: int
    balance_revenue: int
    day_orders: int
    day_revenue: int
    topups: int
    topups_sum: int
