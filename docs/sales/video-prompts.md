# Рекламный ролик AI-Gate — промпты для ИИ-видео (6 × 10 сек = 60 сек)

Комплект для генераторов видео (Sora, Google Veo, Runway Gen-3, Kling,
Pika). Для каждой сцены: визуальный промпт (EN — модели понимают его
точнее), закадровый текст (RU, ~22–25 слов = ~10 сек озвучки), надписи на
экране и подсказки по монтажу.

> **Как пользоваться.** Генерируйте сцены по одной (10 сек — предел
> большинства моделей). Склейте 6 клипов в видеоредакторе, наложите
> закадровый голос и надписи. Сгенерировать сразу минуту одним промптом
> не выйдет — модели не держат консистентность так долго.

---

## Общий стайл-гайд (добавлять в КАЖДЫЙ промпт для единообразия)

```
STYLE: modern corporate tech, clean and trustworthy, not flashy.
Color palette: deep navy blue (#0f172a) and white, with a single
electric-blue (#2563eb) accent and occasional emerald-green (#16a34a)
for "safe" states, red (#dc2626) for "blocked". Soft studio lighting,
shallow depth of field, subtle motion. Cinematic 16:9, 4K, smooth camera.
No text baked into the video (titles added in post). Realistic office
setting in a modern bank / corporate environment.
```

- **Голос за кадром:** спокойный, уверенный, мужской или женский, деловой.
- **Музыка:** сдержанный корпоративный underscore, лёгкое нарастание к сцене 5–6.
- **Логотип AI-Gate:** появляется только в сцене 6.
- **Дисклеймер** мелким шрифтом в сцене 6: «Инструмент снижения риска.
  Оценка соответствия — за юридическим департаментом».

---

## Сцена 1 (0–10 сек) — Проблема: данные утекают

**Visual prompt (EN):**
```
A bank employee at a modern desk copies text from a document labeled
"ДОГОВОР" (contract) and pastes it into a ChatGPT-like web chat on screen.
Close-up of the screen: sensitive-looking rows of numbers and a name.
The pasted text visually "flies" out of the office window as glowing blue
data particles, drifting away into the city skyline. Slight tension in
the air. Over-the-shoulder cinematic shot. [+ STYLE block]
```

**Закадровый текст (RU):**
> «Каждый день сотрудники отправляют во внешние ИИ договоры, базы клиентов,
> исходный код. Почти половина таких запросов содержит конфиденциальные
> данные».

**Надписи на экране:** `~40% запросов к ИИ содержат чувствительные данные`

**Монтаж:** старт крупным планом экрана, отъезд камеры к окну с улетающими
данными.

---

## Сцена 2 (10–20 сек) — Цена: закон и штраф

**Visual prompt (EN):**
```
The drifting blue data particles cross a glowing map outline of Kazakhstan
and continue beyond its border, turning red as they leave. A subtle legal
document and a rising bar chart appear as holographic overlays. Serious,
consequential mood. Slow push-in. [+ STYLE block]
```

**Закадровый текст (RU):**
> «Для Казахстана это трансграничная передача персональных данных. Закон
> девяносто четыре-пять, штраф — до двух тысяч МРП и риск блокировки».

**Надписи на экране:** `Закон РК № 94-V · ст. 12 / 16` → `до 2000 МРП ≈ 8,65 млн ₸`

**Монтаж:** акцент на пересечении границы (синий → красный) — ключевой образ.

---

## Сцена 3 (20–30 сек) — Запрет не работает

**Visual prompt (EN):**
```
Split scene: on the left, an IT admin blocks a website (a red "BLOCKED"
padlock on a corporate monitor). On the right, the same employee simply
pulls out a personal smartphone and uses the AI chat anyway, unnoticed.
The contrast is clear. Handheld, realistic documentary feel. [+ STYLE block]
```

**Закадровый текст (RU):**
> «Просто запретить нельзя — сотрудники продолжат с личных устройств. Вы
> теряете и контроль, и продуктивность, которую даёт искусственный интеллект».

**Надписи на экране:** `Запрет = потеря видимости, не снижение риска`

**Монтаж:** резкий сплит-скрин, подчёркивающий бесполезность блокировки.

---

## Сцена 4 (30–40 сек) — Решение: шлюз (ядро ролика)

**Visual prompt (EN):**
```
A sleek glowing gateway / portal appears between the employee's desk and
the outside world. Blue data particles carrying a name and ID numbers flow
INTO the gateway; on the other side they emerge as neutral placeholder
tokens "[PERSON_1]" "[IIN_1]" glowing safely green. The real data stays
inside a protected vault icon within the building. Elegant, satisfying
transformation. Smooth cinematic dolly. [+ STYLE block]
```

**Закадровый текст (RU):**
> «AI-Gate встаёт между сотрудником и ИИ. Персональные данные заменяются
> плейсхолдерами до отправки. Наружу уходит обезличенный текст — данные
> остаются у вас».

**Надписи на экране:** `Клиент [PERSON_1], ИИН [IIN_1]` · `данные не покидают периметр`

**Монтаж:** самая важная сцена — показать превращение данных в токены
крупно и наглядно.

---

## Сцена 5 (40–50 сек) — Обратимость + контроль

**Visual prompt (EN):**
```
The AI's answer flows back through the gateway; the green placeholder
tokens transform back into the real name and numbers on the employee's
screen — she smiles, satisfied, productive. Quick cut to a clean analytics
dashboard on a large screen: counters ticking up, a green "chain intact"
checkmark, a red "BLOCKED: secret detected" row. Confident, positive mood.
[+ STYLE block]
```

**Закадровый текст (RU):**
> «В ответе данные восстанавливаются — сотрудник работает как обычно. А
> служба безопасности видит полный аудит: что замаскировано, что
> заблокировано, ноль утечек».

**Надписи на экране:** `Ответ — с реальными данными` · `Аудит для регулятора · 0 утечек`

**Монтаж:** переход от «человек доволен» к «дашборд под контролем».

---

## Сцена 6 (50–60 сек) — Бренд и призыв

**Visual prompt (EN):**
```
The camera pulls back to reveal the whole modern bank office working
calmly and safely, a subtle protective blue dome of light over the
building. Center screen resolves to a clean logo lockup on deep navy
background. Minimal, premium, trustworthy. Gentle final light bloom.
[+ STYLE block]
```

**Закадровый текст (RU):**
> «AI-Gate — право безопасно использовать ИИ всей организацией. On-prem,
> в вашем периметре, под закон Казахстана. Запросите демонстрацию».

**Надписи на экране:**
- крупно: `AI-Gate` · `Безопасный ИИ для вашей организации`
- мелко (дисклеймер): `Инструмент снижения риска. Оценка соответствия — за юридическим департаментом.`
- призыв: `Демо — 15 минут · [контакт]`

**Монтаж:** отъезд на общий план → лого → контакты. Музыка разрешается.

---

## Единый промпт «одним куском» (если генератор берёт длинный сценарий)

Для моделей вроде Sora, умеющих в раскадровку одним запросом:

```
Create a 60-second corporate ad, 6 shots of ~10 seconds each, consistent
style throughout. STYLE: modern corporate tech, trustworthy; deep navy
(#0f172a) + white, electric-blue (#2563eb) accent, green for safe / red
for blocked; cinematic 16:9 4K, soft lighting, smooth camera; no baked-in
text.

SHOT 1: bank employee pastes a contract into a web AI chat; sensitive data
flies out the window as blue particles.
SHOT 2: particles cross the outline of Kazakhstan's border and turn red;
legal document and rising fine chart appear.
SHOT 3: split screen — admin blocks a site on the left; employee uses AI on
a personal phone on the right.
SHOT 4: a glowing gateway between desk and outside world; data flows in as a
name and ID, comes out as green tokens "[PERSON_1]" "[IIN_1]"; real data
stays in a vault inside the building.
SHOT 5: the AI answer flows back and tokens turn into real data on screen;
employee smiles; cut to an analytics dashboard with rising counters, a green
checkmark, a red "BLOCKED" row.
SHOT 6: pull back to the whole office safe under a blue protective dome;
resolve to a clean logo on navy background.
```

---

## Продакшн-заметки

- **Тайминг озвучки:** держите закадр в ~22–25 словах на сцену — это ровно
  10 секунд в спокойном темпе. Длиннее — придётся ускорять голос.
- **Надписи — только в пост-обработке**, не в самом видео: ИИ-генераторы
  пишут текст с ошибками, особенно кириллицу.
- **Плейсхолдеры `[IIN_1]`/`[PERSON_1]`** тоже накладывайте титрами поверх —
  так они будут читаемы и точны.
- **Никаких реальных данных** в кадре: имена и номера — синтетические
  (например, ИИН из демо-стенда, который не принадлежит реальному лицу).
- **Версии под площадки:** 60 сек — сайт/презентация; для соцсетей
  вырежьте сцены 1-4-6 в вертикали 9:16 на 30 секунд.
- **Логотип:** если фирменного стиля ещё нет — временно текстовый логотип
  «AI-Gate» тем же navy/blue, замените позже.
