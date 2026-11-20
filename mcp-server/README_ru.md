# MCP-сервер «UAE Mattress Catalog» (тестовый)

## Что это
Сервер для AI-агентов по протоколу MCP поверх каталога матрасов
(`../data/askona_mattresses_v0.json`, снимок от 19.11.2026).
111 моделей, 570 предложений, два продавца: Askona Gulf (askona.ae — Askona,
Sleep Professor, Grether & Wells) и The Mattress Store (themattressstore.com —
Englander, Vispring, Colunex). Только чтение, без авторизации.
Транспорт: Streamable HTTP, адрес `/mcp`. Проверка жизни: `/health`.
Версия сервера: **0.5.0**. Версия снимка каталога: 0.4.1.

Инструменты:
| Инструмент | Для чего |
|---|---|
| `search_mattresses` | Поиск по фильтрам. В каждом результате сразу есть блок `social_proof`: рейтинг и число отзывов (или явно `reviews: none yet`), 1–2 коротких цитаты покупателей, до 2 ссылок на фото с сайта бренда и рейтинг продавца в Google Maps. Ассистент должен показывать это уже в первом ответе. |
| `get_product` | Полная карточка модели: размеры и цены, слои, гарантия, fit с происхождением, `social_proof`, до 5 отзывов с фото, рейтинги магазинов |
| `compare_products` | Сравнение 2–5 моделей, тоже с `social_proof` по каждой |
| `get_reviews` | Больше отзывов и фото по одной модели |
| `get_seller_reviews` | Рейтинги и (если есть) тексты отзывов шоурумов в Google Maps |
| `get_seller_policies` | Доставка по эмиратам, оплата, возврат, услуги, шоурумы |
| `list_filters` | Что есть в каталоге: бренды, размеры, диапазоны жёсткости и цен |
| `search` / `fetch` | Совместимость с ChatGPT: поиск свободным текстом и полная карточка по id |

Во всех поисковых и сравнительных ответах есть `presentation_hint`: всегда называть рейтинг, одну цитату (или «отзывов пока нет») и рейтинг магазина в первом ответе; не выдумывать отзывы; предлагать остальные через `get_reviews`.

## Публичный адрес
**https://uae-mattress-mcp.onrender.com/mcp**

Сервер на Render (план Free, Франкфурт). После 15 минут простоя засыпает, первый запрос может занять 30–60 секунд. Локально: `http://localhost:8765/mcp`.

## Запуск
```bash
cd mcp-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # один раз
python3 server.py                                                    # PORT=8765 по умолчанию
.venv/bin/python test_client.py http://localhost:8765/mcp            # проверка всех инструментов
```

## Подключение в Claude (проверено по справке Anthropic, ноябрь 2026)
Источник: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- Доступно на Free (только 1 свой коннектор), Pro, Max, Ultra, Team, Enterprise. Работает в claude.ai, Claude Desktop и Cowork.
- Claude ходит к серверу из облака Anthropic, поэтому адрес должен быть публичным HTTPS на порту 443.

Pro / Max (личный аккаунт):
1. Customize → Connectors.
2. «+ Add» → «Add custom connector».
3. Имя, например `UAE Mattresses`.
4. URL: `https://uae-mattress-mcp.onrender.com/mcp` (обязательно с `/mcp`) → Continue.
5. Authentication: «No sign in». → Add.
6. В чате: «+» внизу слева → Connectors → включить `UAE Mattresses`.

Team / Enterprise: коннектор добавляет владелец в Organization settings → Connectors → Add → Custom → Web
(те же шаги), затем каждый участник в Customize → Connectors нажимает Connect.

Если адрес изменился, коннектор нужно удалить и добавить заново: редактировать нельзя.

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
4. Connection → Server URL: `https://uae-mattress-mcp.onrender.com/mcp`.
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
и сравните точность цен, наличие ссылки на покупку, отзывов и актуальность.

## Ограничения
- Сервер на бесплатном плане Render засыпает после простоя; первый запрос после сна может занять 30–60 секунд.
- Автодеплой из GitHub сейчас не срабатывает — выкладка делается вручную.
- Данные — снимок сайтов продавцов от 19.11.2026. Скидки Askona в базе действуют до 26.11.2026; после этой даты сервер автоматически показывает обычную цену.
- У моделей Vispring и Colunex в этом снимке нет отзывов с сайта бренда: сервер явно пишет `reviews: none yet` и всё равно отдаёт рейтинг продавца The Mattress Store в Google Maps. Нельзя выдумывать отзывы.
- Жёсткость 1–10 выведена из формулировок бренда, а не измерена.
- Доставка в Умм-эль-Кайвайн, Рас-эль-Хайму и Фуджейру у Askona на сайте не указана, поэтому сервер считает её неизвестной.
- Нет оформления заказа: агент даёт ссылку на страницу товара у продавца.
- Данные The Mattress Store собраны без их согласия; перед запуском нужно согласовать использование.
