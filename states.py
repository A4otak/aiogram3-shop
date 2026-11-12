from aiogram.fsm.state import State, StatesGroup


class AddCategory(StatesGroup):
    name = State()


class AddProduct(StatesGroup):
    category = State()
    product = State()
    name = State()
    price = State()
    content = State()


class CryptoBotSetup(StatesGroup):
    token = State()


class BalanceEdit(StatesGroup):
    user_id = State()
    action = State()
    amount = State()
    confirm = State()


class TopUp(StatesGroup):
    amount = State()


class Purchase(StatesGroup):
    quantity = State()
