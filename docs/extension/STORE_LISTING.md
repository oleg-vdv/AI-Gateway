# Карточка в Chrome Web Store и Edge Add-ons

Тексты для заполнения формы. Публикует владелец аккаунта разработчика.

## Название
AI-Gate DLP

## Краткое описание (до 132 символов, берётся из manifest.json)
Скрывает ИИН, ФИО, телефоны, адреса, счета и ключи перед отправкой в ChatGPT, Claude и Gemini. Работает со шлюзом AI-Gate.

## Категория
Workflow & Planning (Инструменты для работы)

## Язык
Русский. Второй язык карточки — английский (текст ниже).

## Подробное описание (RU)

Сотрудники вставляют в ChatGPT письма клиентов, договоры и анкеты, и вместе с ними за рубеж уходят ИИН, ФИО, телефоны и номера счетов. AI-Gate DLP скрывает эти данные до отправки, а в ответе ИИ показывает настоящие значения только вам.

Как это работает:
• вы пишете сообщение как обычно;
• перед отправкой расширение передаёт текст на шлюз AI-Gate вашей организации или вашего компьютера;
• шлюз заменяет данные метками: «Клиент [PERSON_1], ИИН [IIN_1]»;
• ChatGPT, Claude или Gemini получают только метки;
• панель расширения показывает, что за каждой меткой, и копирует ответ с настоящими данными — в саму страницу сайта значения не вписываются.

Что скрывается:
• ИИН и БИН (с проверкой контрольной суммы);
• ФИО, включая казахские формы (-улы, -кызы) и латиницу;
• телефоны РК и стран Центральной Азии, email;
• номера удостоверений и паспортов, даты рождения;
• адреса и госномера автомобилей;
• номера карт и счетов IBAN, суммы;
• ключи API и пароли: такие сообщения не отправляются совсем.

Если шлюз недоступен, сообщение не уходит. Лучше переспросить, чем отправить данные открытым текстом.

Нужен шлюз AI-Gate: его можно запустить на своём компьютере или на сервере организации. Код открыт: github.com/oleg-vdv/AI-Gateway

Ограничения: загруженные файлы пока не проверяются, расширение предупреждает о них.

## Detailed description (EN)

AI-Gate DLP masks personal data before it reaches ChatGPT, Claude or Gemini. National IDs (Kazakhstan IIN/BIN with checksum validation), names, phone numbers, emails, ID documents, addresses, card and IBAN numbers are replaced with placeholders like [PERSON_1] by your AI-Gate gateway. The AI service only sees placeholders, and the extension's own panel shows what each placeholder stands for — real values are never written into the site's page. If the gateway is unreachable, nothing is sent. Requires an AI-Gate gateway (self-hosted, open source: github.com/oleg-vdv/AI-Gateway).

## Политика конфиденциальности (URL)
https://github.com/oleg-vdv/AI-Gateway/blob/main/docs/extension/PRIVACY.md

## Обоснование разрешений (поле «Permission justification»)

- **storage**: хранит адрес шлюза и ключ канала на устройстве.
- **host permission localhost**: связь со шлюзом AI-Gate на компьютере пользователя.
- **optional host permissions**: запрашиваются только для адреса шлюза, который указал пользователь или администратор.
- **content scripts на chatgpt.com, chat.openai.com, claude.ai, gemini.google.com**: перехват сообщения до отправки и восстановление значений в ответе.
- **Single purpose**: защита персональных данных в сообщениях к сервисам ИИ.
- **Remote code**: не используется.
- **Data usage**: текст сообщения передаётся только на шлюз, указанный пользователем; разработчик данные не получает и не продаёт.

## Что нужно приложить

- Иконка 128×128: `extension/icons/icon-128.png`.
- Скриншоты 1280×800, минимум 1. Снять на тестовых данных: сообщение с ИИН и ФИО до отправки и тот же диалог с выключенным расширением, где видны метки.
- Архив: `python -I tools/pack_extension.py` → `dist/aigate-dlp-chrome-<версия>.zip`.
