import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import config
from database import catalog, orders, settings, users
from database.models import BalanceReason, OrderStatus, PaymentKind, PaymentMethod, PaymentRecord, User
from keyboards.admin import (
    admin_menu_kb,
    back_kb,
    balance_action_kb,
    category_pick_kb,
    confirm_kb,
    product_choice_kb,
)
from keyboards.callbacks import AdminAction, AdminCB
from services import cryptobot
from states import AddCategory, AddProduct, BalanceEdit, CryptoBotSetup
from utils.messaging import render
from utils.money import format_rub, parse_rub
from utils.pagination import PAGE_SIZE, Page

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.from_user.id.in_(config.admin_ids))
router.callback_query.filter(F.from_user.id.in_(config.admin_ids))

Target = Message | CallbackQuery
Transition = Callable[[Target, FSMContext], Awaitable[None]]

MENU_TEXT = "🛠 <b>Админ-панель</b>\n\nВыберите действие:"
MAX_FILE_SIZE = 1024 * 1024

TESTNET_HINT = (
    "Ошибка API. Если токен из @CryptoBot — выключите CRYPTOBOT_TESTNET в .env. "
    "Если из @CryptoTestnetBot — включите."
)

CONTENT_HELP = (
    "Отправьте содержимое товара <b>текстом</b> или файлом <b>.txt</b>.\n\n"
    "Каждая новая строка — это отдельная единица товара, например один аккаунт. "
    "Покупатель может взять сразу несколько штук — бот выдаст столько строк, сколько куплено.\n\n"
    "Пример для аккаунтов:\n"
    "<code>email1@mail.com;pass1;cookie1\n"
    "email2@mail.com;pass2;cookie2</code>\n\n"
    "Формат строки произвольный: ключи, ссылки, логин:пароль и т. д. "
    "Для больших партий используйте файл — длинные сообщения Telegram может разбить на части."
)

STATUS_LABELS = {
    OrderStatus.PENDING: "ожидает оплаты",
    OrderStatus.PAID: "оплачен",
    OrderStatus.CANCELLED: "отменён",
    OrderStatus.REFUNDED: "возврат на баланс",
}
METHOD_LABELS = {
    PaymentMethod.CRYPTOBOT: "CryptoBot",
    PaymentMethod.BALANCE: "баланс",
}


async def show_menu(target: Target, state: FSMContext) -> None:
    await state.clear()
    await render(target, MENU_TEXT, admin_menu_kb())


async def prompt_product_category(target: Target, state: FSMContext, page: int | None = None) -> None:
    await state.set_state(AddProduct.category)
    data = await state.get_data()
    current = Page.of(await catalog.count_categories(), page or data.get("category_page", 1))
    await state.update_data(category_page=current.number)

    categories = await catalog.list_categories(PAGE_SIZE, current.offset)
    await render(target, "Выберите категорию для товара:", category_pick_kb(categories, current))


async def prompt_product_choice(target: Target, state: FSMContext, page: int | None = None) -> None:
    await state.set_state(AddProduct.product)
    data = await state.get_data()
    category_id = data["category_id"]
    current = Page.of(await catalog.count_restock_groups(category_id), page or data.get("product_page", 1))
    await state.update_data(product_page=current.number, restock=False)

    groups = await catalog.list_restock_groups(category_id, PAGE_SIZE, current.offset)
    hint = (
        "Выберите товар, который нужно пополнить, или создайте новый:"
        if groups
        else "В этой категории пока нет товаров — создайте первый:"
    )
    await render(
        target,
        f"Категория: <b>{escape(data['category_name'])}</b>\n\n{hint}",
        product_choice_kb(groups, current),
    )


async def prompt_product_name(target: Target, state: FSMContext) -> None:
    await state.set_state(AddProduct.name)
    data = await state.get_data()
    await render(
        target,
        f"Категория: <b>{escape(data['category_name'])}</b>\n\nВведите название товара:",
        back_kb(),
    )


async def prompt_product_price(target: Target, state: FSMContext) -> None:
    await state.set_state(AddProduct.price)
    data = await state.get_data()
    await render(
        target,
        f"Товар: <b>{escape(data['product_name'])}</b>\n\n"
        "Введите цену в рублях, например <code>199</code> или <code>99.90</code>:",
        back_kb(),
    )


async def prompt_product_content(target: Target, state: FSMContext) -> None:
    await state.set_state(AddProduct.content)
    data = await state.get_data()
    header = ""
    if data.get("restock"):
        header = (
            f"📦 Пополнение: <b>{escape(data['product_name'])}</b> "
            f"({format_rub(data['price'])})\n\n"
        )
    await render(target, f"{header}{CONTENT_HELP}", back_kb())


async def back_from_content(target: Target, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("restock"):
        await prompt_product_choice(target, state)
    else:
        await prompt_product_price(target, state)


async def prompt_balance_user(target: Target, state: FSMContext) -> None:
    await state.set_state(BalanceEdit.user_id)
    await render(target, "Введите Telegram ID пользователя:", back_kb())


async def prompt_balance_action(target: Target, state: FSMContext) -> None:
    await state.set_state(BalanceEdit.action)
    data = await state.get_data()
    profile = await users.get_user(data["target_id"])
    total = await orders.count_user_payments(profile.user_id)
    current = Page.of(total, data.get("history_page", 1))
    await state.update_data(history_page=current.number)

    payments = await orders.list_user_payments(profile.user_id, PAGE_SIZE, current.offset)
    await render(target, _user_card(profile, payments, total), balance_action_kb(current))


async def prompt_balance_amount(target: Target, state: FSMContext) -> None:
    await state.set_state(BalanceEdit.amount)
    data = await state.get_data()
    verb = "выдать пользователю" if data["operation"] == AdminAction.GIVE else "забрать у пользователя"
    await render(target, f"Введите сумму в рублях, которую нужно {verb}:", back_kb())


async def prompt_balance_confirm(target: Target, state: FSMContext) -> None:
    await state.set_state(BalanceEdit.confirm)
    data = await state.get_data()
    profile = await users.get_user(data["target_id"])
    giving = data["operation"] == AdminAction.GIVE
    new_balance = profile.balance + data["amount"] if giving else profile.balance - data["amount"]
    text = (
        "⚠️ <b>Подтвердите операцию</b>\n\n"
        f"Пользователь: {escape(profile.full_name)} (<code>{profile.user_id}</code>)\n"
        f"Операция: {'выдать' if giving else 'забрать'} <b>{format_rub(data['amount'])}</b>\n"
        f"Баланс сейчас: {format_rub(profile.balance)}\n"
        f"Баланс после: <b>{format_rub(new_balance)}</b>"
    )
    await render(target, text, confirm_kb())


BACK_TRANSITIONS: dict[str, Transition] = {
    AddCategory.name.state: show_menu,
    AddProduct.category.state: show_menu,
    AddProduct.product.state: prompt_product_category,
    AddProduct.name.state: prompt_product_choice,
    AddProduct.price.state: prompt_product_name,
    AddProduct.content.state: back_from_content,
    CryptoBotSetup.token.state: show_menu,
    BalanceEdit.user_id.state: show_menu,
    BalanceEdit.action.state: prompt_balance_user,
    BalanceEdit.amount.state: prompt_balance_action,
    BalanceEdit.confirm.state: prompt_balance_amount,
}


def _payment_line(record: PaymentRecord) -> str:
    status = STATUS_LABELS[record.status]
    created = record.created_at[:16]
    if record.kind is PaymentKind.TOPUP:
        return f"➕ Пополнение №{record.id} · {format_rub(record.amount)} · {status} · {created}"
    return (
        f"🧾 Заказ №{record.id} · {record.quantity} шт. · {format_rub(record.amount)} · "
        f"{METHOD_LABELS[record.method]} · {status} · {created}"
    )


def _user_card(profile: User, payments: Sequence[PaymentRecord], total: int) -> str:
    username = f"@{escape(profile.username)}" if profile.username else "без username"
    lines = [
        f"👤 <b>{escape(profile.full_name)}</b> ({username})",
        f"ID: <code>{profile.user_id}</code>",
        f"Баланс: <b>{format_rub(profile.balance)}</b>",
        "",
        f"<b>История платежей</b> (всего: {total}):",
        *(_payment_line(record) for record in payments),
    ]
    if not payments:
        lines.append("платежей пока нет")
    lines += [
        "",
        "Если покупатель оплатил счёт, а товар или баланс не получил, сверьте платёж в CryptoBot "
        "и при необходимости выдайте сумму на баланс.",
    ]
    return "\n".join(lines)


async def _read_content(message: Message, bot: Bot) -> str | None:
    document = message.document
    if document is None:
        return message.text

    if not (document.file_name or "").lower().endswith(".txt"):
        await message.answer("Поддерживаются только файлы в формате .txt.")
        return None
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.answer("Файл слишком большой. Максимальный размер — 1 МБ.")
        return None

    buffer = await bot.download(document)
    raw = buffer.getvalue()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


async def _notify_balance_change(bot: Bot, user_id: int, delta: int, balance: int) -> None:
    action = "пополнил ваш баланс на" if delta > 0 else "списал с вашего баланса"
    text = (
        f"💰 Администратор {action} <b>{format_rub(abs(delta))}</b>.\n"
        f"Текущий баланс: <b>{format_rub(balance)}</b>"
    )
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        logger.warning("Не удалось уведомить пользователя %s об изменении баланса", user_id)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await show_menu(message, state)


@router.callback_query(AdminCB.filter(F.action == AdminAction.MENU))
async def open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await show_menu(callback, state)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.BACK))
async def go_back(callback: CallbackQuery, state: FSMContext) -> None:
    transition = BACK_TRANSITIONS.get(await state.get_state() or "", show_menu)
    await transition(callback, state)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.ADD_CATEGORY))
async def start_add_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddCategory.name)
    await render(callback, "Введите название новой категории:", back_kb())
    await callback.answer()


@router.message(AddCategory.name, F.text)
async def save_category(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 64:
        await message.answer("Название должно содержать от 1 до 64 символов.", reply_markup=back_kb())
        return
    if not await catalog.create_category(name):
        await message.answer("Категория с таким названием уже существует.", reply_markup=back_kb())
        return

    logger.info("Админ %s создал категорию %r", message.from_user.id, name)
    await state.clear()
    await render(message, f"✅ Категория «{escape(name)}» добавлена.\n\n{MENU_TEXT}", admin_menu_kb())


@router.callback_query(AdminCB.filter(F.action == AdminAction.ADD_PRODUCT))
async def start_add_product(callback: CallbackQuery, state: FSMContext) -> None:
    if await catalog.count_categories() == 0:
        await callback.answer("Сначала добавьте хотя бы одну категорию.", show_alert=True)
        return
    await prompt_product_category(callback, state, page=1)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.CATEGORY_PAGE), AddProduct.category)
async def change_category_page(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await prompt_product_category(callback, state, page=callback_data.value)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.PICK_CATEGORY), AddProduct.category)
async def pick_product_category(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    category = await catalog.get_category(callback_data.value)
    if category is None:
        await callback.answer("Категория не найдена.", show_alert=True)
        return
    await state.update_data(category_id=category.id, category_name=category.name, product_page=1)
    await prompt_product_choice(callback, state, page=1)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.PRODUCT_PAGE), AddProduct.product)
async def change_product_page(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await prompt_product_choice(callback, state, page=callback_data.value)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.NEW_PRODUCT), AddProduct.product)
async def start_new_product(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(restock=False)
    await prompt_product_name(callback, state)
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.PICK_PRODUCT), AddProduct.product)
async def pick_existing_product(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    group = await catalog.get_product_group(callback_data.value)
    if group is None:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    await state.update_data(product_name=group.name, price=group.price, restock=True)
    await prompt_product_content(callback, state)
    await callback.answer()


@router.message(AddProduct.name, F.text)
async def receive_product_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 100:
        await message.answer("Название должно содержать от 1 до 100 символов.", reply_markup=back_kb())
        return
    await state.update_data(product_name=name)
    await prompt_product_price(message, state)


@router.message(AddProduct.price, F.text)
async def receive_product_price(message: Message, state: FSMContext) -> None:
    try:
        price = parse_rub(message.text or "")
    except ValueError:
        await message.answer(
            "Не удалось распознать цену. Пример: <code>199</code> или <code>99.90</code>.",
            reply_markup=back_kb(),
        )
        return
    await state.update_data(price=price)
    await prompt_product_content(message, state)


@router.message(AddProduct.content, F.text | F.document)
async def receive_product_content(message: Message, state: FSMContext, bot: Bot) -> None:
    raw = await _read_content(message, bot)
    if raw is None:
        return

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        await message.answer("Не найдено ни одной непустой строки. Отправьте данные ещё раз.", reply_markup=back_kb())
        return

    data = await state.get_data()
    added = await catalog.add_products(data["category_id"], data["product_name"], data["price"], lines)
    category = await catalog.get_category(data["category_id"])
    logger.info(
        "Админ %s добавил %s шт. товара %r в категорию %s (пополнение: %s)",
        message.from_user.id,
        added,
        data["product_name"],
        data["category_id"],
        bool(data.get("restock")),
    )

    await state.clear()
    title = "Товар пополнен" if data.get("restock") else "Товар добавлен"
    text = (
        f"✅ {title}: добавлено позиций — <b>{added}</b>\n"
        f"Товар: {escape(data['product_name'])}\n"
        f"Цена: {format_rub(data['price'])}\n"
        f"Категория: {escape(data['category_name'])} (в наличии: {category.stock if category else added} шт.)\n\n"
        f"{MENU_TEXT}"
    )
    await render(message, text, admin_menu_kb())


def _api_mode() -> str:
    return "testnet" if config.cryptobot_testnet else "mainnet"


def _token_status(db_token: str | None) -> str:
    if db_token:
        return f"из базы (<code>{escape(db_token[:8])}…</code>)"
    if config.cryptobot_token:
        return f"из .env (<code>{escape(config.cryptobot_token[:8])}…</code>)"
    return "не задан"


def _token_error_text(error: cryptobot.CryptoBotError) -> str:
    if error.status is None:
        reason = "Не удалось связаться с CryptoBot. Проверьте подключение к интернету и попробуйте снова."
    else:
        reason = f"{TESTNET_HINT}\n\nОтвет API: HTTP {error.status}."
    return (
        "❌ <b>Токен не прошёл проверку</b>\n\n"
        f"{reason}\n\n"
        f"Сейчас бот работает в режиме: <b>{_api_mode()}</b>."
    )


@router.callback_query(AdminCB.filter(F.action == AdminAction.CRYPTOBOT))
async def start_cryptobot_setup(callback: CallbackQuery, state: FSMContext) -> None:
    db_token = await settings.get_setting(settings.CRYPTOBOT_TOKEN_KEY)
    await state.set_state(CryptoBotSetup.token)
    await render(
        callback,
        "🔐 <b>CryptoBot</b>\n\n"
        f"Текущий токен: {_token_status(db_token)}\n"
        f"Режим API: <b>{_api_mode()}</b> (переменная CRYPTOBOT_TESTNET в .env)\n\n"
        "Отправьте API-токен приложения: @CryptoBot → Crypto Pay → Create App → API Token.\n"
        "Токен из базы приоритетнее токена из .env. Сообщение с токеном будет удалено после проверки.",
        back_kb(),
    )
    await callback.answer()


@router.message(CryptoBotSetup.token, F.text)
async def save_cryptobot_token(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    with suppress(TelegramAPIError):
        await message.delete()

    try:
        app_name = await cryptobot.get_me(token)
    except cryptobot.CryptoBotError as exc:
        logger.warning(
            "Токен CryptoBot не прошёл проверку (админ %s): HTTP %s, режим %s, ответ: %s",
            message.from_user.id,
            exc.status if exc.status is not None else "нет ответа",
            _api_mode(),
            exc,
        )
        await message.answer(_token_error_text(exc), reply_markup=back_kb())
        return

    await settings.set_setting(settings.CRYPTOBOT_TOKEN_KEY, token)
    logger.info(
        "Админ %s обновил токен CryptoBot (приложение %r, режим %s)",
        message.from_user.id,
        app_name,
        _api_mode(),
    )
    await state.clear()
    await render(
        message,
        f"✅ CryptoBot подключён. Приложение: <b>{escape(app_name)}</b> ({_api_mode()})\n\n{MENU_TEXT}",
        admin_menu_kb(),
    )


@router.callback_query(AdminCB.filter(F.action == AdminAction.STATS))
async def show_stats(callback: CallbackQuery) -> None:
    stats = await orders.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {stats.users}\n"
        f"Сумма на балансах пользователей: {format_rub(stats.balances)}\n\n"
        f"Продаж всего: <b>{stats.orders}</b>\n"
        f"Выручка: <b>{format_rub(stats.revenue)}</b>\n"
        f"  · через CryptoBot: {format_rub(stats.crypto_revenue)}\n"
        f"  · с баланса: {format_rub(stats.balance_revenue)}\n"
        f"За последние 24 часа: {stats.day_orders} шт. на {format_rub(stats.day_revenue)}\n"
        f"Пополнения баланса через CryptoBot: {stats.topups} шт. на {format_rub(stats.topups_sum)}\n\n"
        f"Склад: в наличии {stats.in_stock} шт., продано {stats.sold_items} шт.\n\n"
        "<i>Прибыль считается как сумма оплаченных заказов, себестоимость не учитывается.</i>"
    )
    await render(callback, text, back_kb())
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == AdminAction.BALANCE))
async def start_balance_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await prompt_balance_user(callback, state)
    await callback.answer()


@router.message(BalanceEdit.user_id, F.text)
async def receive_balance_user(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("ID должен состоять только из цифр.", reply_markup=back_kb())
        return

    profile = await users.get_user(int(raw))
    if profile is None:
        await message.answer(
            "Пользователь не найден. Он должен хотя бы раз запустить бота.", reply_markup=back_kb()
        )
        return

    await state.update_data(target_id=profile.user_id, history_page=1)
    await prompt_balance_action(message, state)


@router.callback_query(AdminCB.filter(F.action == AdminAction.HISTORY_PAGE), BalanceEdit.action)
async def change_history_page(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await state.update_data(history_page=callback_data.value)
    await prompt_balance_action(callback, state)
    await callback.answer()


@router.callback_query(
    AdminCB.filter(F.action.in_({AdminAction.GIVE, AdminAction.TAKE})), BalanceEdit.action
)
async def choose_balance_operation(
    callback: CallbackQuery, callback_data: AdminCB, state: FSMContext
) -> None:
    await state.update_data(operation=callback_data.action.value)
    await prompt_balance_amount(callback, state)
    await callback.answer()


@router.message(BalanceEdit.amount, F.text)
async def receive_balance_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_rub(message.text or "")
    except ValueError:
        await message.answer(
            "Не удалось распознать сумму. Пример: <code>150</code> или <code>49.90</code>.",
            reply_markup=back_kb(),
        )
        return

    data = await state.get_data()
    profile = await users.get_user(data["target_id"])
    if data["operation"] == AdminAction.TAKE and amount > profile.balance:
        await message.answer(
            f"Нельзя забрать больше, чем есть на балансе ({format_rub(profile.balance)}).",
            reply_markup=back_kb(),
        )
        return

    await state.update_data(amount=amount)
    await prompt_balance_confirm(message, state)


@router.callback_query(AdminCB.filter(F.action == AdminAction.CONFIRM), BalanceEdit.confirm)
async def apply_balance_change(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    giving = data["operation"] == AdminAction.GIVE
    delta = data["amount"] if giving else -data["amount"]
    reason = BalanceReason.ADMIN_GRANT if giving else BalanceReason.ADMIN_WITHDRAW

    new_balance = await users.change_balance(data["target_id"], delta, reason, admin_id=callback.from_user.id)
    if new_balance is None:
        await callback.answer("Недостаточно средств на балансе пользователя.", show_alert=True)
        return

    logger.info(
        "Админ %s изменил баланс пользователя %s на %s коп. (%s)",
        callback.from_user.id,
        data["target_id"],
        delta,
        reason,
    )
    await _notify_balance_change(bot, data["target_id"], delta, new_balance)
    await state.clear()
    await render(
        callback,
        f"✅ Готово. Новый баланс пользователя: <b>{format_rub(new_balance)}</b>\n\n{MENU_TEXT}",
        admin_menu_kb(),
    )
    await callback.answer()


@router.message(StateFilter(*BACK_TRANSITIONS))
async def unexpected_input(message: Message) -> None:
    await message.answer(
        "Не удалось обработать сообщение. Следуйте подсказке выше или нажмите «⬅️ Назад».",
        reply_markup=back_kb(),
    )
