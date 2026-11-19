# MCP-сервер «UAE Mattress Catalog» (тестовый)

## Что это
Сервер для AI-агентов по протоколу MCP поверх базы матрасов (`../data/askona_mattresses_v0.json`,
57 моделей, 246 предложений, снимок askona.ae от 19.11.2026). Только чтение, без авторизации.
Транспорт: Streamable HTTP, адрес `/mcp`. Проверка жизни: `/health`.

Инструменты:
| Инструмент | Для чего |
|---|---|
| `search_mattresses` | Поиск по фильтрам: жёсткость 1–10 (двусторонние совпадают по любой стороне), размер или класс размера, цена AED (с учётом действующей скидки), конструкция, взрослый/детский, бренд, эмират доставки, оплата (tabby/tamara/cod/card), свойства (cooling, orthopedic, memory_foam…), только в наличии, сортировка, лимит |
| `get_product` | Полная карточка модели: все размеры и цены, слои, гарантия, доказательства свойств, источник и дата |
| `compare_products` | Сравнение 2–5 моделей, при желании для конкретного размера |
| `get_seller_policies` | Доставка по эмиратам, оплата, возврат, услуги, шоурумы |
| `list_filters` | Что вообще есть в каталоге: бренды, размеры, диапазоны жёсткости и цен |
| `search` / `fetch` | Совместимость с ChatGPT (deep research / company knowledge): поиск свободным текстом и полная карточка по id |

## Публичный адрес
Публичный адрес: **пока нет** — см. «Ограничения». Локально: `http://localhost:8765/mcp`.
Когда туннель заработает, `./start.sh` печатает адрес вида `https://<слова>.trycloudflare.com/mcp`
и пишет его в `PUBLIC_URL.txt`.

## Запуск
```bash
cd mcp-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # один раз
./start.sh            # перезапускает сервер и туннель Cloudflare, печатает URL
.venv/bin/python test_client.py https://<адрес>/mcp   # проверка всех инструментов
```

## Подключение в Claude (проверено по справке Anthropic, ноябрь 2026)
Источник: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- Доступно на Free (только 1 свой коннектор), Pro, Max, Ultra, Team, Enterprise. Работает в claude.ai, Claude Desktop и Cowork.
- Claude ходит к серверу из облака Anthropic, поэтому адрес должен быть публичным HTTPS на порту 443 (trycloudflare подходит).

Pro / Max (личный аккаунт):
1. Customize → Connectors.
2. «+ Add» → «Add custom connector».
3. Имя, например `UAE Mattresses`.
4. URL: `https://<адрес>/mcp` (обязательно с `/mcp`) → Continue.
5. Authentication: «No sign in». → Add.
6. В чате: «+» внизу слева → Connectors → включить `UAE Mattresses`.

Team / Enterprise: коннектор добавляет владелец в Organization settings → Connectors → Add → Custom → Web
(те же шаги), затем каждый участник в Customize → Connectors нажимает Connect.

Если адрес изменился (перезапуск туннеля), коннектор нужно удалить и добавить заново: редактировать нельзя.

## Подключение в ChatGPT (проверено по документации OpenAI, ноябрь 2026)
Источники: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt ,
https://developers.openai.com/api/docs/guides/custom-mcp-server
- Только веб-версия ChatGPT. Режим разработчика больше включать не нужно, сервер добавляется через Plugins.
- Тарифы по справке OpenAI: Business и Enterprise/Edu — полная поддержка (в Business добавлять могут только
  админы и владельцы; в Enterprise/Edu нужно право «Create plugins with MCPs»). Pro — можно подключать
  свои MCP-серверы с правами только на чтение (нашему серверу этого достаточно). Plus, Go и Free в справке
  не упомянуты: если пункта «Add custom MCP server» нет, причина в тарифе.

Шаги:
1. chatgpt.com → Plugins.
2. «+» → «Add custom MCP server».
3. Имя `UAE Mattresses`, описание по желанию.
4. Connection → Server URL: `https://<адрес>/mcp`.
5. Authentication: «No authentication».
6. Отметить «I understand and want to continue» → «Create as a plugin», проверить список инструментов.
7. Установить (Install) плагин, открыть новый чат, ввести `@` и выбрать `UAE Mattresses`.

Все инструменты помечены как только для чтения (`readOnlyHint`), поэтому ChatGPT не должен спрашивать
подтверждение на каждый вызов. Подсказка из документации OpenAI: в запросе прямо писать
«используй только UAE Mattresses, не используй веб-поиск».

## 10 тестовых запросов
1. Подбери матрас средней жёсткости 160x200 до 2000 AED с доставкой в Абу-Даби и оплатой через Tabby.
2. Нужен детский матрас до 500 AED, доставка в Дубай. Что есть в наличии?
3. Какой самый жёсткий матрас размера king (180x200)? Покажи 3 варианта с ценами.
4. Сравни Sleep Professor Arizona Duo и Askona Ortho Hard в размере 160x200.
5. Какие условия возврата и доставки у продавца? Можно вернуть товар со скидкой?
6. Find me a cooling orthopedic mattress 180x200, cheapest first, with a link to buy.
7. I sleep on my side and like soft beds. Suggest 3 soft mattresses 160x200 under 3000 AED.
8. What's the best memory foam mattress for two people under 2500 AED delivered to Sharjah?
9. Which mattresses have two sides with different firmness? Show prices for 180x200.
10. Is delivery to Fujairah available? If not, what are my options?

Для честного сравнения прогоните те же запросы в новом чате без коннектора (обычный веб-поиск)
и сравните точность цен, наличие ссылки на покупку и актуальность.

## Ограничения
- **Публичного адреса сейчас нет.** Сеть этого компьютера (бота) пропускает наружу только обычный HTTPS.
  Быстрому туннелю Cloudflare нужен исходящий порт 7844 (QUIC или HTTP/2), он заблокирован: адрес
  выдаётся, но открывается ошибкой 1033. Решения: запустить `start.sh` на машине, где порт 7844 открыт
  (например, ваш компьютер или любой VPS), или задеплоить сервер на хостинг (Render, Fly.io, Railway,
  Cloudflare/Vercel; нужен аккаунт).
- Адрес trycloudflare временный: меняется при каждом перезапуске `start.sh`, после чего коннекторы
  в Claude и ChatGPT надо пересоздать. Сервер работает, пока включён компьютер, на котором он запущен.
- Данные — снимок askona.ae от 19.11.2026. Скидки в базе действуют до 26.11.2026; после этой даты сервер
  автоматически показывает обычную цену. Наличие у 139 из 246 предложений неизвестно (`unknown`).
- Жёсткость 1–10 выведена из формулировок бренда, а не измерена. Доставка в Умм-эль-Кайвайн,
  Рас-эль-Хайму и Фуджейру не указана на сайте, поэтому сервер считает её неизвестной.
- Нет оформления заказа: агент даёт ссылку на страницу товара askona.ae.
