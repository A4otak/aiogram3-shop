import logging
from collections.abc import Sequence
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message, User

from config import config
from database import catalog, orders, topups, users
from database.models import (
    OrderStatus,
    PaymentMethod,
    ProductGroup,
    PurchaseRecord,
    PurchaseResult,
    PurchaseStatus,
    TopupStatus,
)
from keyboards.callbacks import (
    BUY_ALL,
    BuyCB,
    BuyStep,
    CatalogCB,
    CategoryCB,
    DownloadCB,
    HelpCB,
    HistoryCB,
    HomeCB,
    NoopCB,
    OrderCB,
    ProductCB,
    ProfileCB,
    PurchaseCB,
    QtyCustomCB,
    TopUpCheckCB,
    TopUpCustomCB,
    TopUpMenuCB,
    TopUpPresetCB,
)
from keyboards.user import (
    BTN_CATALOG,
    BTN_HELP,
    BTN_PROFILE,
    categories_kb,
    confirm_purchase_kb,
    delivery_kb,
    empty_catalog_kb,
    help_kb,
    history_kb,
    invoice_kb,
    main_menu_inline_kb,
    main_menu_kb,
    product_kb,
    products_kb,
    profile_kb,
    purchase_view_kb,
    quantity_prompt_kb,
    to_catalog_kb,
    to_profile_kb,
    topup_amount_kb,
    topup_invoice_kb,
    topup_menu_kb,
)
from services import cryptobot
from states import Purchase, TopUp
from utils.messaging import render
from utils.money import format_rub, parse_rub, to_decimal
from utils.pagination import PAGE_SIZE, Page, page_number_of

logger = logging.getLogger(__name__)
router = Router(name="user")

DIVIDER = "━━━━━━━━━━━━━━━━━━"
MIN_TOPUP = 5_000
MAX_TOPUP = 5_000_000

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
CONTENT_PREVIEW = 1200
SHORT_PREVIEW = 200
# Данные аккаунтов, ключи и cookie чувствительны к регистру: перевод в верхний регистр
# делает их нерабочими, поэтому в файле заглавными пишутся только заголовки.
UPPERCASE_FILE_DATA = False

PURCHASE_ALERTS = {
    PurchaseStatus.INSUFFICIENT_FUNDS: "Недостаточно средств на балансе.",
    PurchaseStatus.OUT_OF_STOCK: "К сожалению, товара уже не хватает на складе.",
    PurchaseStatus.ALREADY_PROCESSED: "Этот заказ уже обработан.",
}
METHOD_LABELS = {
    PaymentMethod.CRYPTOBOT: "💳 CryptoBot",
    PaymentMethod.BALANCE: "💰 Баланс",
}


def _home_text(name: str, returning: bool) -> str:
    greeting = "С возвращением" if returning else "Добро пожаловать"
    return (
        f"👋 <b>{greeting}, {escape(name)}!</b>\n"
        f"{DIVIDER}\n"
        "🛒 <b>Магазин цифровых товаров</b>\n\n"
        "⚡️ Мгновенная выдача после оплаты\n"
        "🔒 Безопасная оплата через CryptoBot\n"
        "💰 Баланс для быстрых покупок\n"
        f"{DIVIDER}\n"
        "Выберите раздел 👇"
    )


def _help_text() -> str:
    text = (
        "❓ <b>Помощь</b>\n"
        f"{DIVIDER}\n"
        "<b>Как купить товар:</b>\n"
        "1️⃣ Откройте «🛍 Каталог» и выберите товар\n"
        "2️⃣ Выберите количество и подтвердите покупку\n"
        "3️⃣ Оплатите счёт через CryptoBot или с баланса\n"
        "4️⃣ Нажмите «🔄 Проверить оплату» — товар придёт сразу\n"
        f"{DIVIDER}\n"
        "<b>Если что-то пошло не так:</b>\n"
        "💬 Оплатили, а товар не пришёл — напишите в поддержку и укажите номер заказа.\n"
        "📜 Купленные данные всегда доступны в «Профиль → История покупок»."
    )
    if config.support_username:
        text += f"\n\n📩 Поддержка: @{escape(config.support_username)}"
    return text


def _product_text(group: ProductGroup) -> str:
    return (
        f"🛒 <b>{escape(group.name)}</b>\n"
        f"{DIVIDER}\n"
        f"📁 Категория: {escape(group.category_name)}\n"
        f"💵 Цена за 1 шт.: <b>{format_rub(group.price)}</b>\n"
        f"📦 В наличии: <b>{group.stock} шт.</b>\n"
        f"{DIVIDER}\n"
        "Выберите количество 👇"
    )


def _format_contents(contents: Sequence[str], limit: int) -> str:
    shown: list[str] = []
    size = 0
    for line in contents:
        if shown and size + len(line) > limit:
            break
        shown.append(line)
        size += len(line) + 1

    block = f"<code>{escape(chr(10).join(shown))}</code>"
    hidden = len(contents) - len(shown)
    if hidden:
        block += f"\n<i>…и ещё {hidden} шт. — полный список в файле</i>"
    return block


def _order_document(order_id: int, product_name: str, contents: Sequence[str]) -> BufferedInputFile:
    lines = [line.upper() for line in contents] if UPPERCASE_FILE_DATA else list(contents)
    body = "\n".join(
        [
            f"ЗАКАЗ №{order_id}",
            f"ТОВАР: {product_name.upper()}",
            f"КОЛИЧЕСТВО: {len(lines)}",
            "=" * 32,
            "ДАННЫЕ:",
            *lines,
        ]
    )
    return BufferedInputFile(body.encode("utf-8"), filename=f"order_{order_id}.txt")


def _success_text(order_id: int, product_name: str, contents: Sequence[str], limit: int) -> str:
    return (
        "✅ <b>Покупка оформлена!</b>\n"
        f"{DIVIDER}\n"
        f"🧾 Заказ: <b>№{order_id}</b>\n"
        f"🛒 Товар: {escape(product_name)}\n"
        f"🔢 Количество: <b>{len(contents)} шт.</b>\n"
        f"{DIVIDER}\n"
        "📦 <b>Ваши данные:</b>\n"
        f"{_format_contents(contents, limit)}\n"
        f"{DIVIDER}\n"
        "📎 Копия данных — в прикреплённом файле.\n"
        "<i>Заказ всегда доступен в «Профиль → История покупок».</i>"
    )


def _purchase_text(purchase: PurchaseRecord, contents: Sequence[str], limit: int) -> str:
    return (
        f"📦 <b>Заказ №{purchase.order_id}</b>\n"
        f"{DIVIDER}\n"
        f"🛒 Товар: <b>{escape(purchase.product_name)}</b>\n"
        f"📁 Категория: {escape(purchase.category_name)}\n"
        f"🔢 Количество: {purchase.quantity} шт.\n"
        f"💵 Сумма: {format_rub(purchase.price)}\n"
        f"💳 Оплата: {METHOD_LABELS[purchase.method]}\n"
        f"🕒 Дата: {(purchase.paid_at or '')[:16]} UTC\n"
        f"{DIVIDER}\n"
        "📦 <b>Ваши данные:</b>\n"
        f"{_format_contents(contents, limit)}"
    )


async def _alert(target: Message | CallbackQuery, text: str) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text)


async def _render_home(target: Message | CallbackQuery, tg_user: User) -> None:
    await render(target, _home_text(tg_user.full_name, returning=True), main_menu_inline_kb())


async def _render_help(target: Message | CallbackQuery) -> None:
    await render(target, _help_text(), help_kb())


async def _render_profile(target: Message | CallbackQuery, tg_user: User) -> None:
    profile, _ = await users.register_user(tg_user.id, tg_user.username, tg_user.full_name)
    purchases, spent = await orders.get_purchase_totals(profile.user_id)
    text = (
        "👤 <b>Профиль</b>\n"
        f"{DIVIDER}\n"
        f"🆔 ID: <code>{profile.user_id}</code>\n"
        f"🙂 Имя: {escape(profile.full_name)}\n"
        f"💰 Баланс: <b>{format_rub(profile.balance)}</b>\n"
        f"{DIVIDER}\n"
        f"🛒 Заказов: <b>{purchases}</b>\n"
        f"💸 Потрачено: <b>{format_rub(spent)}</b>"
    )
    await render(target, text, profile_kb())


async def _render_catalog(target: Message | CallbackQuery, page: int = 1) -> None:
    total = await catalog.count_categories()
    if total == 0:
        text = (
            "🛍 <b>Каталог</b>\n"
            f"{DIVIDER}\n"
            "😔 <b>Пока здесь пусто</b>\n\n"
            "Товары скоро появятся — загляните чуть позже.\n"
            "Вопросы по наличию можно уточнить в разделе «❓ Помощь»."
        )
        await render(target, text, empty_catalog_kb())
        return

    current = Page.of(total, page)
    categories = await catalog.list_categories(PAGE_SIZE, current.offset)
    text = f"🛍 <b>Каталог</b>\n{DIVIDER}\nВыберите категорию 👇"
    await render(target, text, categories_kb(categories, current))


async def _render_category(target: Message | CallbackQuery, category_id: int, page: int = 1) -> None:
    category = await catalog.get_category(category_id)
    if category is None:
        await _render_catalog(target)
        return

    total = await catalog.count_product_groups(category_id)
    current = Page.of(total, page)
    groups = await catalog.list_product_groups(category_id, PAGE_SIZE, current.offset)
    catalog_page = page_number_of(await catalog.count_categories_before(category.name))

    header = f"📁 <b>{escape(category.name)}</b>\n{DIVIDER}\n"
    if groups:
        text = f"{header}📦 Всего в наличии: <b>{category.stock} шт.</b>\n\nВыберите товар 👇"
    else:
        text = (
            f"{header}😔 <b>Сейчас здесь нет товаров</b>\n\n"
            "Загляните позже или выберите другую категорию."
        )
    await render(target, text, products_kb(groups, category_id, current, catalog_page))


async def _render_product(target: Message | CallbackQuery, group: ProductGroup) -> None:
    category_page = page_number_of(await catalog.count_groups_before(group))
    await render(target, _product_text(group), product_kb(group, category_page))


async def _load_available_group(callback: CallbackQuery, product_id: int) -> ProductGroup | None:
    group = await catalog.get_product_group(product_id)
    if group is None or group.stock == 0:
        await callback.answer("К сожалению, товар уже закончился.", show_alert=True)
        if group is None:
            await _render_catalog(callback)
        else:
            await _render_category(callback, group.category_id)
        return None
    return group


async def _check_stock(callback: CallbackQuery, group: ProductGroup, quantity: int) -> bool:
    if 1 <= quantity <= group.stock:
        return True
    await callback.answer(f"В наличии только {group.stock} шт.", show_alert=True)
    await _render_product(callback, group)
    return False


async def _render_confirm(
    target: Message | CallbackQuery, tg_user: User, group: ProductGroup, quantity: int
) -> None:
    profile, _ = await users.register_user(tg_user.id, tg_user.username, tg_user.full_name)
    total = group.price * quantity
    lines = [
        "🧾 <b>Подтверждение покупки</b>",
        DIVIDER,
        f"🛒 Товар: <b>{escape(group.name)}</b>",
        f"📁 Категория: {escape(group.category_name)}",
        f"💵 Цена за 1 шт.: {format_rub(group.price)}",
        f"🔢 Количество: <b>{quantity} шт.</b>",
        f"💰 Итого к оплате: <b>{format_rub(total)}</b>",
        DIVIDER,
        f"👛 Ваш баланс: {format_rub(profile.balance)}",
    ]
    if profile.balance < total:
        shortage = format_rub(total - profile.balance)
        lines.append(f"💡 Для оплаты с баланса не хватает {shortage} — его можно пополнить в профиле.")
    lines += ["", "❓ <b>Точно хотите купить?</b>"]

    await render(target, "\n".join(lines), confirm_purchase_kb(group, quantity, profile.balance))


async def _deliver_purchase(
    callback: CallbackQuery, order_id: int, product_name: str, contents: Sequence[str]
) -> None:
    chat_id = callback.from_user.id
    document = _order_document(order_id, product_name, contents)
    text = _success_text(order_id, product_name, contents, CONTENT_PREVIEW)

    if isinstance(callback.message, Message):
        with suppress(TelegramAPIError):
            await callback.message.delete()

    if len(text) <= CAPTION_LIMIT:
        await callback.bot.send_document(
            chat_id, document, caption=text, reply_markup=delivery_kb(order_id)
        )
        return

    if len(text) > MESSAGE_LIMIT:
        text = _success_text(order_id, product_name, contents, SHORT_PREVIEW)
    await callback.bot.send_message(chat_id, text)
    await callback.bot.send_document(
        chat_id,
        document,
        caption="📎 Полные данные заказа — в файле.",
        reply_markup=delivery_kb(order_id),
    )


async def _finish_purchase(callback: CallbackQuery, result: PurchaseResult) -> None:
    if result.status in PURCHASE_ALERTS:
        await callback.answer(PURCHASE_ALERTS[result.status], show_alert=True)
        return

    if result.status is PurchaseStatus.REFUNDED:
        text = (
            "😔 <b>Товара не хватило</b>\n"
            f"{DIVIDER}\n"
            "Пока обрабатывался платёж, нужного количества на складе уже не осталось.\n\n"
            "💰 Оплаченная сумма <b>полностью зачислена на ваш баланс</b> — "
            "ею можно оплатить другой товар или меньшее количество."
        )
        await render(callback, text, to_catalog_kb())
    else:
        await _deliver_purchase(callback, result.order_id, result.product_name or "", result.contents)
    await callback.answer()


@router.message(CommandStart())
async def cmd_start(message: Message, event_from_user: User, state: FSMContext) -> None:
    await state.clear()
    _, created = await users.register_user(
        event_from_user.id, event_from_user.username, event_from_user.full_name
    )
    await message.answer(
        "⌨️ <i>Быстрое меню включено — кнопки под полем ввода.</i>", reply_markup=main_menu_kb()
    )
    await message.answer(
        _home_text(event_from_user.full_name, returning=not created),
        reply_markup=main_menu_inline_kb(),
    )


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _render_help(message)


@router.message(F.text == BTN_PROFILE)
async def show_profile_message(message: Message, event_from_user: User, state: FSMContext) -> None:
    await state.clear()
    await _render_profile(message, event_from_user)


@router.message(F.text == BTN_CATALOG)
async def show_catalog_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _render_catalog(message)


@router.callback_query(NoopCB.filter())
async def ignore_button(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(HomeCB.filter())
async def show_home(callback: CallbackQuery, event_from_user: User, state: FSMContext) -> None:
    await state.clear()
    await _render_home(callback, event_from_user)
    await callback.answer()


@router.callback_query(HelpCB.filter())
async def show_help(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_help(callback)
    await callback.answer()


@router.callback_query(ProfileCB.filter())
async def show_profile(callback: CallbackQuery, event_from_user: User, state: FSMContext) -> None:
    await state.clear()
    await _render_profile(callback, event_from_user)
    await callback.answer()


@router.callback_query(CatalogCB.filter())
async def show_catalog(callback: CallbackQuery, callback_data: CatalogCB, state: FSMContext) -> None:
    await state.clear()
    await _render_catalog(callback, callback_data.page)
    await callback.answer()


@router.callback_query(CategoryCB.filter())
async def show_category(callback: CallbackQuery, callback_data: CategoryCB) -> None:
    await _render_category(callback, callback_data.category_id, callback_data.page)
    await callback.answer()


@router.callback_query(ProductCB.filter())
async def show_product(callback: CallbackQuery, callback_data: ProductCB, state: FSMContext) -> None:
    await state.clear()
    group = await _load_available_group(callback, callback_data.product_id)
    if group is None:
        return
    await _render_product(callback, group)
    await callback.answer()


@router.callback_query(QtyCustomCB.filter())
async def ask_custom_quantity(
    callback: CallbackQuery, callback_data: QtyCustomCB, state: FSMContext
) -> None:
    group = await _load_available_group(callback, callback_data.product_id)
    if group is None:
        return

    await state.set_state(Purchase.quantity)
    await state.update_data(product_id=group.id)
    text = (
        "✏️ <b>Своё количество</b>\n"
        f"{DIVIDER}\n"
        f"🛒 {escape(group.name)} · {format_rub(group.price)} за шт.\n"
        f"📦 В наличии: <b>{group.stock} шт.</b>\n\n"
        f"Отправьте число от 1 до {group.stock}."
    )
    await render(callback, text, quantity_prompt_kb(group.id))
    await callback.answer()


@router.message(Purchase.quantity, F.text, ~F.text.startswith("/"))
async def receive_quantity(message: Message, event_from_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    group = await catalog.get_product_group(data["product_id"])
    if group is None or group.stock == 0:
        await state.clear()
        await message.answer("К сожалению, товар уже закончился.", reply_markup=to_catalog_kb())
        return

    raw = (message.text or "").strip()
    if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= group.stock):
        await message.answer(
            "⚠️ <b>Некорректное количество</b>\n"
            f"Введите целое число от 1 до {group.stock}.",
            reply_markup=quantity_prompt_kb(group.id),
        )
        return

    await state.clear()
    await _render_confirm(message, event_from_user, group, int(raw))


@router.callback_query(BuyCB.filter(F.step == BuyStep.ASK))
async def confirm_purchase(
    callback: CallbackQuery, callback_data: BuyCB, event_from_user: User, state: FSMContext
) -> None:
    await state.clear()
    group = await _load_available_group(callback, callback_data.product_id)
    if group is None:
        return

    quantity = group.stock if callback_data.qty == BUY_ALL else callback_data.qty
    if not await _check_stock(callback, group, quantity):
        return

    await _render_confirm(callback, event_from_user, group, quantity)
    await callback.answer()


@router.callback_query(BuyCB.filter(F.step == BuyStep.CRYPTO))
async def pay_with_cryptobot(
    callback: CallbackQuery, callback_data: BuyCB, event_from_user: User
) -> None:
    group = await _load_available_group(callback, callback_data.product_id)
    if group is None:
        return

    quantity = callback_data.qty
    if not await _check_stock(callback, group, quantity):
        return

    token = await cryptobot.get_token()
    if not token:
        await callback.answer("Оплата через CryptoBot временно недоступна.", show_alert=True)
        return

    total = group.price * quantity
    await users.register_user(event_from_user.id, event_from_user.username, event_from_user.full_name)
    order_id = await orders.create_pending_order(event_from_user.id, group.id, total, quantity)

    try:
        invoice = await cryptobot.create_invoice(
            token=token,
            amount=to_decimal(total),
            description=f"{group.name} × {quantity} ({group.category_name})",
            payload=str(order_id),
            expires_in=config.invoice_ttl,
        )
    except cryptobot.CryptoBotError:
        logger.exception("Не удалось создать счёт для заказа %s", order_id)
        await orders.cancel_pending_order(order_id)
        await callback.answer("Не удалось создать счёт. Попробуйте позже.", show_alert=True)
        return

    await orders.attach_invoice(order_id, invoice.invoice_id)
    text = (
        f"💳 <b>Счёт №{order_id}</b>\n"
        f"{DIVIDER}\n"
        f"🛒 Товар: <b>{escape(group.name)}</b>\n"
        f"🔢 Количество: {quantity} шт.\n"
        f"💵 Сумма: <b>{format_rub(total)}</b>\n"
        f"{DIVIDER}\n"
        "1️⃣ Нажмите «💳 Оплатить счёт»\n"
        "2️⃣ После оплаты вернитесь и нажмите «🔄 Проверить оплату»\n\n"
        f"⏳ Счёт действителен {config.invoice_ttl // 60} мин."
    )
    await render(callback, text, invoice_kb(order_id, invoice.pay_url, group.id, quantity))
    await callback.answer()


@router.callback_query(BuyCB.filter(F.step == BuyStep.BALANCE))
async def pay_with_balance(
    callback: CallbackQuery, callback_data: BuyCB, event_from_user: User
) -> None:
    result = await orders.purchase_with_balance(
        event_from_user.id, callback_data.product_id, callback_data.qty
    )
    if result.status is PurchaseStatus.SUCCESS:
        logger.info(
            "Заказ %s (%s шт.) оплачен с баланса пользователем %s",
            result.order_id,
            len(result.contents),
            event_from_user.id,
        )
    await _finish_purchase(callback, result)


@router.callback_query(OrderCB.filter())
async def check_payment(
    callback: CallbackQuery, callback_data: OrderCB, event_from_user: User
) -> None:
    order = await orders.get_order(callback_data.order_id)
    if order is None or order.user_id != event_from_user.id or order.invoice_id is None:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    if order.status is not OrderStatus.PENDING:
        await callback.answer("Этот заказ уже обработан.", show_alert=True)
        return

    token = await cryptobot.get_token()
    if not token:
        await callback.answer("Проверка оплаты временно недоступна.", show_alert=True)
        return

    try:
        invoice = await cryptobot.get_invoice(token, order.invoice_id)
    except cryptobot.CryptoBotError:
        logger.exception("Не удалось проверить счёт заказа %s", order.id)
        await callback.answer("Не удалось проверить оплату. Попробуйте ещё раз.", show_alert=True)
        return

    if invoice is None or invoice.status == "expired":
        await orders.cancel_pending_order(order.id)
        text = (
            "⌛ <b>Срок действия счёта истёк</b>\n"
            f"{DIVIDER}\n"
            "Оформите покупку заново — это займёт пару секунд."
        )
        await render(callback, text, to_catalog_kb())
        await callback.answer()
        return

    if invoice.status != "paid":
        await callback.answer("Оплата ещё не поступила. Попробуйте через минуту.", show_alert=True)
        return

    result = await orders.fulfill_paid_order(order.id)
    logger.info("Заказ %s оплачен через CryptoBot: %s", order.id, result.status)
    await _finish_purchase(callback, result)


async def _render_history(callback: CallbackQuery, tg_user: User, page: int) -> None:
    total = await orders.count_user_purchases(tg_user.id)
    current = Page.of(total, page)
    purchases = await orders.list_user_purchases(tg_user.id, PAGE_SIZE, current.offset)

    if purchases:
        lines = [
            "📜 <b>История покупок</b>",
            DIVIDER,
            f"Всего заказов: <b>{total}</b>. Нажмите на заказ, чтобы открыть данные 👇",
            "",
        ]
        for purchase in purchases:
            lines.append(f"🧾 <b>№{purchase.order_id}</b> · {escape(purchase.product_name)}")
            lines.append(
                f"      🔢 {purchase.quantity} шт. · 💵 {format_rub(purchase.price)} · "
                f"{METHOD_LABELS[purchase.method]}"
            )
            lines.append(f"      🕒 {(purchase.paid_at or '')[:16]} UTC")
        text = "\n".join(lines)
    else:
        text = (
            "📜 <b>История покупок</b>\n"
            f"{DIVIDER}\n"
            "🫙 Здесь пока пусто.\n\n"
            "Ваши покупки появятся здесь сразу после первого заказа."
        )
    await render(callback, text, history_kb(purchases, current))


@router.callback_query(HistoryCB.filter())
async def show_history(callback: CallbackQuery, callback_data: HistoryCB, event_from_user: User) -> None:
    await _render_history(callback, event_from_user, callback_data.page)
    await callback.answer()


@router.callback_query(PurchaseCB.filter())
async def show_purchase(
    callback: CallbackQuery, callback_data: PurchaseCB, event_from_user: User
) -> None:
    purchase = await orders.get_purchase(callback_data.order_id, event_from_user.id)
    if purchase is None:
        await callback.answer("Покупка не найдена.", show_alert=True)
        return

    contents = await orders.get_order_contents(purchase.order_id, event_from_user.id)
    text = _purchase_text(purchase, contents, CONTENT_PREVIEW)
    if len(text) > MESSAGE_LIMIT:
        text = _purchase_text(purchase, contents, SHORT_PREVIEW)
    await render(callback, text, purchase_view_kb(purchase.order_id, callback_data.page))
    await callback.answer()


@router.callback_query(DownloadCB.filter())
async def download_order(
    callback: CallbackQuery, callback_data: DownloadCB, event_from_user: User
) -> None:
    purchase = await orders.get_purchase(callback_data.order_id, event_from_user.id)
    if purchase is None:
        await callback.answer("Покупка не найдена.", show_alert=True)
        return

    contents = await orders.get_order_contents(purchase.order_id, event_from_user.id)
    document = _order_document(purchase.order_id, purchase.product_name, contents)
    await callback.bot.send_document(
        event_from_user.id,
        document,
        caption=f"📎 Заказ <b>№{purchase.order_id}</b> — данные в файле.",
    )
    await callback.answer("Файл отправлен 📎")


async def _send_topup_invoice(target: Message | CallbackQuery, tg_user: User, kopecks: int) -> None:
    token = await cryptobot.get_token()
    if not token:
        await _alert(target, "Пополнение временно недоступно. Подробности — в разделе «Помощь».")
        return

    await users.register_user(tg_user.id, tg_user.username, tg_user.full_name)
    topup_id = await topups.create_topup(tg_user.id, kopecks)

    try:
        invoice = await cryptobot.create_invoice(
            token=token,
            amount=to_decimal(kopecks),
            description=f"Пополнение баланса #{topup_id}",
            payload=f"topup:{topup_id}",
            expires_in=config.invoice_ttl,
        )
    except cryptobot.CryptoBotError:
        logger.exception("Не удалось создать счёт для пополнения %s", topup_id)
        await topups.cancel_topup(topup_id)
        await _alert(target, "Не удалось создать счёт. Попробуйте позже.")
        return

    await topups.attach_invoice(topup_id, invoice.invoice_id)
    text = (
        f"💳 <b>Пополнение баланса №{topup_id}</b>\n"
        f"{DIVIDER}\n"
        f"💵 Сумма: <b>{format_rub(kopecks)}</b>\n"
        f"{DIVIDER}\n"
        "1️⃣ Нажмите «💳 Оплатить счёт»\n"
        "2️⃣ После оплаты вернитесь и нажмите «🔄 Проверить оплату»\n\n"
        f"⏳ Счёт действителен {config.invoice_ttl // 60} мин."
    )
    await render(target, text, topup_invoice_kb(topup_id, invoice.pay_url))
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.callback_query(TopUpMenuCB.filter())
async def show_topup_menu(callback: CallbackQuery, event_from_user: User, state: FSMContext) -> None:
    await state.clear()
    profile, _ = await users.register_user(
        event_from_user.id, event_from_user.username, event_from_user.full_name
    )
    text = (
        "💳 <b>Пополнение баланса</b>\n"
        f"{DIVIDER}\n"
        f"💰 Сейчас на балансе: <b>{format_rub(profile.balance)}</b>\n\n"
        "Выберите сумму или введите свою 👇\n"
        f"<i>От {format_rub(MIN_TOPUP)} до {format_rub(MAX_TOPUP)}. "
        "Оплата через CryptoBot, зачисление сразу после платежа.</i>"
    )
    await render(callback, text, topup_menu_kb())
    await callback.answer()


@router.callback_query(TopUpPresetCB.filter())
async def topup_preset(
    callback: CallbackQuery, callback_data: TopUpPresetCB, event_from_user: User, state: FSMContext
) -> None:
    await state.clear()
    await _send_topup_invoice(callback, event_from_user, callback_data.amount * 100)


@router.callback_query(TopUpCustomCB.filter())
async def topup_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUp.amount)
    text = (
        "✏️ <b>Своя сумма</b>\n"
        f"{DIVIDER}\n"
        "Отправьте сумму пополнения в рублях, например <code>250</code>.\n"
        f"<i>От {format_rub(MIN_TOPUP)} до {format_rub(MAX_TOPUP)}.</i>"
    )
    await render(callback, text, topup_amount_kb())
    await callback.answer()


@router.message(TopUp.amount, F.text, ~F.text.startswith("/"))
async def receive_topup_amount(message: Message, event_from_user: User, state: FSMContext) -> None:
    try:
        kopecks = parse_rub(message.text or "")
    except ValueError:
        kopecks = 0

    if not MIN_TOPUP <= kopecks <= MAX_TOPUP:
        await message.answer(
            "⚠️ <b>Некорректная сумма</b>\n"
            f"Введите число от {format_rub(MIN_TOPUP)} до {format_rub(MAX_TOPUP)}, например <code>250</code>.",
            reply_markup=topup_amount_kb(),
        )
        return

    await state.clear()
    await _send_topup_invoice(message, event_from_user, kopecks)


@router.callback_query(TopUpCheckCB.filter())
async def check_topup(
    callback: CallbackQuery, callback_data: TopUpCheckCB, event_from_user: User
) -> None:
    topup = await topups.get_topup(callback_data.topup_id)
    if topup is None or topup.user_id != event_from_user.id or topup.invoice_id is None:
        await callback.answer("Пополнение не найдено.", show_alert=True)
        return
    if topup.status is not TopupStatus.PENDING:
        await callback.answer("Это пополнение уже обработано.", show_alert=True)
        return

    token = await cryptobot.get_token()
    if not token:
        await callback.answer("Проверка оплаты временно недоступна.", show_alert=True)
        return

    try:
        invoice = await cryptobot.get_invoice(token, topup.invoice_id)
    except cryptobot.CryptoBotError:
        logger.exception("Не удалось проверить счёт пополнения %s", topup.id)
        await callback.answer("Не удалось проверить оплату. Попробуйте ещё раз.", show_alert=True)
        return

    if invoice is None or invoice.status == "expired":
        await topups.cancel_topup(topup.id)
        text = (
            "⌛ <b>Срок действия счёта истёк</b>\n"
            f"{DIVIDER}\n"
            "Создайте новое пополнение в профиле."
        )
        await render(callback, text, to_profile_kb())
        await callback.answer()
        return

    if invoice.status != "paid":
        await callback.answer("Оплата ещё не поступила. Попробуйте через минуту.", show_alert=True)
        return

    balance = await topups.fulfill_topup(topup.id)
    if balance is None:
        await callback.answer("Это пополнение уже обработано.", show_alert=True)
        return

    logger.info("Пополнение %s на %s коп. зачислено пользователю %s", topup.id, topup.amount, topup.user_id)
    text = (
        "✅ <b>Баланс пополнен!</b>\n"
        f"{DIVIDER}\n"
        f"➕ Зачислено: <b>{format_rub(topup.amount)}</b>\n"
        f"💰 Текущий баланс: <b>{format_rub(balance)}</b>"
    )
    await render(callback, text, to_profile_kb())
    await callback.answer()
