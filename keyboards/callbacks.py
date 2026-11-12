from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

# В BuyCB значение qty=0 означает «купить всё»: фактическое количество определяется
# в момент нажатия, чтобы не хранить в кнопке устаревший остаток.
BUY_ALL = 0


class BuyStep(StrEnum):
    ASK = "ask"
    CRYPTO = "crypto"
    BALANCE = "balance"


class AdminAction(StrEnum):
    MENU = "menu"
    BACK = "back"
    ADD_CATEGORY = "add_cat"
    ADD_PRODUCT = "add_prod"
    PICK_CATEGORY = "pick_cat"
    CATEGORY_PAGE = "cat_page"
    PICK_PRODUCT = "pick_prod"
    PRODUCT_PAGE = "prod_page"
    NEW_PRODUCT = "new_prod"
    CRYPTOBOT = "cryptobot"
    STATS = "stats"
    BALANCE = "balance"
    HISTORY_PAGE = "pay_page"
    GIVE = "give"
    TAKE = "take"
    CONFIRM = "confirm"


class NoopCB(CallbackData, prefix="noop"):
    pass


class HomeCB(CallbackData, prefix="home"):
    pass


class CatalogCB(CallbackData, prefix="catalog"):
    page: int = 1


class ProfileCB(CallbackData, prefix="profile"):
    pass


class HelpCB(CallbackData, prefix="help"):
    pass


class HistoryCB(CallbackData, prefix="history"):
    page: int = 1


class PurchaseCB(CallbackData, prefix="purchase"):
    order_id: int
    page: int = 1


class DownloadCB(CallbackData, prefix="download"):
    order_id: int


class TopUpMenuCB(CallbackData, prefix="topup"):
    pass


class TopUpPresetCB(CallbackData, prefix="topup_sum"):
    amount: int


class TopUpCustomCB(CallbackData, prefix="topup_own"):
    pass


class TopUpCheckCB(CallbackData, prefix="topup_chk"):
    topup_id: int


class CategoryCB(CallbackData, prefix="cat"):
    category_id: int
    page: int = 1


class ProductCB(CallbackData, prefix="prod"):
    product_id: int


class QtyCustomCB(CallbackData, prefix="qty_own"):
    product_id: int


class BuyCB(CallbackData, prefix="buy"):
    product_id: int
    step: BuyStep
    qty: int = 1


class OrderCB(CallbackData, prefix="order"):
    order_id: int


class AdminCB(CallbackData, prefix="adm"):
    action: AdminAction
    value: int = 0
