// AI-Gate DLP — content script для ChatGPT / Claude / Gemini.
//
// Перехват отправки в capture-фазе ДО того, как страница обработает Enter
// или клик по кнопке. Текст уходит на шлюз (/v1/check, dry_run), поле ввода
// заменяется обезличенным текстом, после чего отправка повторяется.
// Метки в ответе расшифровываются через /v1/restore в панели расширения;
// страница сайта не меняется (v0.3.2).
//
// Fail-closed: если шлюз недоступен или вернул block/fail_closed,
// отправка НЕ происходит.

(() => {
  "use strict";

  const MARK = "data-aigate-approved";
  const log = (...a) => console.info("[AI-Gate]", ...a);
  const SEP = "\u001e"; // ASCII Record Separator: не встречается в обычном тексте

  // --- ключ диалога ---------------------------------------------------------
  // Новый чат живёт на "/", "/new" или "/app", а после первого сообщения сайт
  // меняет URL на адрес диалога. Пока URL «новый», используем временный ключ
  // и затем привязываем сессию к постоянному адресу.

  const NEW_CHAT_PATHS = new Set(["/", "/new", "/app"]);
  let pendingKey = "pending:" + crypto.randomUUID();
  let lastPath = location.pathname;

  function isNewChat(path) {
    return NEW_CHAT_PATHS.has(path.replace(/\/+$/, "") || "/");
  }

  function convKey() {
    return isNewChat(location.pathname)
      ? pendingKey
      : location.host + location.pathname;
  }

  // Сайт меняет URL после первого сообщения, и адрес «нового чата» бывает
  // разным: "/", "/new", "/app", проект, GPTs, временный чат, ссылка
  // с параметрами. Поэтому сессию переносим по факту: если URL сменился
  // вскоре после нашей отправки, новый адрес наследует сессию.
  let lastSend = null; // { key, time }

  // ChatGPT сначала даёт диалогу временный адрес, а после ответа сервера
  // заменяет его настоящим /c/<uuid>. Временные адреса менялись:
  // /c/WEB:<uuid>, затем /c/local-chatgpt:<uuid> (в адресной строке —
  // %3A вместо двоеточия). Общее у них — двоеточие в идентификаторе, у
  // настоящего адреса его нет. Временный адрес всегда передаёт сессию
  // дальше, сколько бы времени ни прошло.
  const TEMP_PATH_RE = /\/c\/[^/]*(?::|%3A)/i;

  setInterval(() => {
    if (location.pathname === lastPath) return;
    const prevPath = lastPath;
    const prevKey = isNewChat(prevPath) ? pendingKey : location.host + prevPath;
    lastPath = location.pathname;
    const newKey = convKey();

    let fromKey = null;
    if (lastSend && Date.now() - lastSend.time < 10000) fromKey = lastSend.key;
    else if (TEMP_PATH_RE.test(prevPath)) fromKey = prevKey;

    if (fromKey && fromKey !== newKey) {
      log("URL сменился после отправки:", prevPath, "→", location.pathname, "— переношу сессию");
      sendToBackground({ type: "AIGATE_BIND", fromKey, toKey: newKey });
      // с временного адреса ещё будет переход на настоящий — следим дальше
      lastSend = TEMP_PATH_RE.test(location.pathname)
        ? { key: newKey, time: Date.now() }
        : null;
    } else if (isNewChat(lastPath)) {
      pendingKey = "pending:" + crypto.randomUUID(); // пользователь открыл новый чат
    }
    scheduleRestore();
  }, 500);

  // --- поле ввода и кнопка отправки -------------------------------------------

  function findComposer() {
    return (
      document.querySelector("#prompt-textarea") || // ChatGPT
      document.querySelector('div[contenteditable="true"].ProseMirror') || // Claude
      document.querySelector('rich-textarea div[contenteditable="true"]') || // Gemini
      document.querySelector('div[contenteditable="true"][role="textbox"]') ||
      document.querySelector("main textarea")
    );
  }

  const SEND_SELECTORS = [
    'button[data-testid="send-button"]', // ChatGPT
    "#composer-submit-button", // ChatGPT (новая разметка)
    'button[aria-label="Send message"]', // Claude
    'button[aria-label="Отправить сообщение"]',
    "button.send-button", // Gemini
    'button[aria-label*="Send"]',
    'button[aria-label*="Отправить"]',
    'button[aria-label*="Submit"]',
  ];

  function findSendButton() {
    for (const sel of SEND_SELECTORS) {
      const b = document.querySelector(sel);
      if (b) return b;
    }
    return null;
  }

  // v0.3.4: разметка сайтов меняется (Claude сменил поле ввода, и 0.3.3 его не
  // узнал — сообщение ушло без маскировки). Поэтому поле ввода определяется не
  // только по известным селекторам, но и по тому, где пользователь печатает:
  // любое редактируемое поле, похожее на поле сообщения.

  const EDITABLE =
    'textarea, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]';
  const CHAT_HINT_RE = /message|prompt|chat|ask|reply|claude|gemini|сообщ|спрос|напиш|вопрос|ответ/i;
  const SEND_HINT_RE = /send|submit|отправ/i;

  function editableRoot(node) {
    let el = node && (node.nodeType === 1 ? node : node.parentElement);
    if (!el || el.closest("aigate-panel, #aigate-toast")) return null;
    el = el.closest(EDITABLE);
    if (!el) return null;
    // внешний редактируемый блок (у ProseMirror/Tiptap вложенные узлы тоже editable)
    let up = el.parentElement && el.parentElement.closest(EDITABLE);
    while (up) {
      el = up;
      up = el.parentElement && el.parentElement.closest(EDITABLE);
    }
    return el;
  }

  function isSendButton(btn) {
    if (!btn) return false;
    if (btn.matches(SEND_SELECTORS.join(","))) return true;
    const hint = [btn.getAttribute("aria-label"), btn.getAttribute("data-testid"), btn.title]
      .filter(Boolean).join(" ");
    if (SEND_HINT_RE.test(hint)) return true;
    return btn.type === "submit" && !!btn.form && !!btn.form.querySelector(EDITABLE);
  }

  // Поле похоже на поле сообщения ИИ: известный селектор, рядом кнопка
  // отправки или подсказка вроде «Напишите сообщение». Обычные редакторы на
  // странице (документ «Текст» в ChatGPT) под это не подходят — Enter в них
  // просто перевод строки.
  function isChatInput(el) {
    if (!el) return false;
    if (el === findComposer()) return true;
    const hint = ["aria-label", "placeholder", "data-placeholder", "id", "name", "data-testid"]
      .map((a) => el.getAttribute(a) || "").join(" ");
    if (CHAT_HINT_RE.test(hint)) return true;
    // Ближайший общий блок с кнопкой отправки, в котором нет других
    // редакторов: так поле сообщения отличается от редактора документа,
    // который просто лежит на той же странице.
    let scope = el.parentElement;
    for (let depth = 0; scope && depth < 5; depth++, scope = scope.parentElement) {
      const editors = new Set([...scope.querySelectorAll(EDITABLE)].map(editableRoot));
      editors.delete(null);
      if (editors.size > 1) return false;
      if ([...scope.querySelectorAll("button")].some(isSendButton)) return true;
    }
    return false;
  }

  let lastChatInput = null;
  window.addEventListener("focusin", (e) => {
    const root = editableRoot(e.target);
    if (root && isChatInput(root)) lastChatInput = root;
  }, true);

  function activeComposer() {
    const known = findComposer();
    if (known && known.isConnected) return known;
    if (lastChatInput && lastChatInput.isConnected) return lastChatInput;
    const candidates = [...document.querySelectorAll(EDITABLE)]
      .map(editableRoot)
      .filter((el) => el && el.offsetParent !== null && isChatInput(el));
    return candidates.length ? candidates[candidates.length - 1] : null;
  }

  function composerText(el) {
    if (!el) return "";
    return el.tagName === "TEXTAREA" ? el.value : el.innerText;
  }

  function setComposerText(el, text) {
    if (el.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, "value").set;
      setter.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      el.focus();
      const sel = window.getSelection();
      sel.removeAllRanges();
      const range = document.createRange();
      range.selectNodeContents(el);
      sel.addRange(range);
      // execCommand устарел, но это единственная вставка, которую
      // React/ProseMirror видят как пользовательский ввод
      document.execCommand("insertText", false, text);
    }
  }

  // --- уведомления ------------------------------------------------------------

  function notify(text, kind) {
    let el = document.getElementById("aigate-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "aigate-toast";
      el.setAttribute("role", "status");
      el.style.cssText =
        "position:fixed;top:12px;right:12px;z-index:2147483647;padding:10px 16px;" +
        "border-radius:8px;font:13px/1.4 system-ui,sans-serif;color:#fff;max-width:380px;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.25);white-space:pre-wrap";
      document.body.appendChild(el);
    }
    el.style.background =
      kind === "error" ? "#b91c1c" : kind === "ok" ? "#15803d" : "#1d4ed8";
    el.textContent = "AI-Gate: " + text;
    el.style.display = "block";
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.style.display = "none"), kind === "error" ? 9000 : 5000);
  }

  function sendToBackground(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (resp) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(resp || { ok: false, error: "нет ответа от фонового скрипта" });
          }
        });
      } catch (_) {
        // расширение перезагрузили, а страница осталась со старым скриптом
        resolve({ ok: false, error: "расширение обновлено, перезагрузите страницу" });
      }
    });
  }

  // --- перехват отправки ---------------------------------------------------------

  let processing = false;

  function nextFrame() {
    return new Promise((r) => requestAnimationFrame(() => setTimeout(r, 30)));
  }

  function sendButtonFor(composer) {
    const scope = composer.closest("form") || composer.closest("fieldset");
    const near = scope && [...scope.querySelectorAll("button")].find(isSendButton);
    return near || findSendButton();
  }

  async function resubmit(composer, clickedButton) {
    composer.setAttribute(MARK, "1");
    await nextFrame(); // даём React включить кнопку после смены текста
    const btn = clickedButton || sendButtonFor(composer);
    if (btn && !btn.disabled) {
      btn.click();
    } else {
      composer.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true,
      }));
    }
    setTimeout(() => composer.removeAttribute(MARK), 600);
  }

  async function interceptAndResubmit(composer, clickedButton) {
    const original = composerText(composer).trim();
    if (!original) return;
    processing = true;
    try {
      const resp = await sendToBackground({
        type: "AIGATE_CHECK",
        text: original,
        convKey: convKey(),
      });
      if (!resp.ok) {
        notify("шлюз недоступен, отправка заблокирована.\n" + resp.error, "error");
        return;
      }
      const r = resp.result;
      lastSend = { key: convKey(), time: Date.now() };
      log("проверка:", r.verdict, r.entity_counts || {}, "ключ диалога:", lastSend.key);
      if (r.verdict === "blocked" || r.verdict === "fail_closed") {
        notify(r.message || "запрос заблокирован политикой", "error");
        return;
      }
      if (r.verdict === "masked" && r.masked_text != null) {
        setComposerText(composer, r.masked_text);
        const counts = Object.entries(r.entity_counts || {})
          .map(([t, c]) => `${t}×${c}`).join(", ");
        notify("замаскировано: " + counts, "ok");
      }
      await resubmit(composer, clickedButton);
      scheduleRestore();
    } finally {
      processing = false;
    }
  }

  // Слушатели на window в фазе захвата: это самая ранняя точка, раньше
  // любых обработчиков сайта (в том числе повешенных на document).

  function stop(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  window.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    const root = editableRoot(e.target);
    if (!root || !isChatInput(root)) return; // перевод строки в обычном редакторе
    if (root.getAttribute(MARK)) return;
    if (processing) return stop(e);
    if (!composerText(root).trim()) return;
    stop(e);
    interceptAndResubmit(root, null);
  }, true);

  window.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("button");
    if (!isSendButton(btn)) return;
    const scope = btn.closest("form") || btn.closest("fieldset");
    const inScope = scope && [...scope.querySelectorAll(EDITABLE)].map(editableRoot).find(Boolean);
    const composer = inScope || activeComposer();
    if (!composer) {
      // кнопка отправки есть, а поля, откуда она отправляет, расширение не нашло:
      // не знаем, что уйдёт, — значит, не отпускаем (fail-closed)
      stop(e);
      notify("не удалось найти поле сообщения на этой странице. Отправка остановлена, " +
        "чтобы данные не ушли без проверки. Обновите страницу или расширение.", "error");
      return;
    }
    if (composer.getAttribute(MARK)) return;
    if (processing) return stop(e);
    if (!composerText(composer).trim()) return;
    stop(e);
    interceptAndResubmit(composer, btn);
  }, true);

  window.addEventListener("submit", (e) => {
    const root = [...e.target.querySelectorAll(EDITABLE)].map(editableRoot).find(Boolean);
    if (!root || root.getAttribute(MARK) || !composerText(root).trim()) return;
    stop(e);
    if (!processing) interceptAndResubmit(root, null);
  }, true);

  // Сторож: если на сайте ИИ расширение не видит ни одного поля сообщения,
  // защита фактически не работает — об этом нужно сказать, а не молчать.
  log("активно на", location.host, "версия", chrome.runtime.getManifest().version);
  let warned = false;
  setInterval(() => {
    const ok = !!activeComposer();
    if (!ok && !warned && document.readyState === "complete") {
      warned = true;
      log("поле сообщения не найдено на", location.host);
      notify("не вижу поле сообщения на этой странице — защита может не работать. " +
        "Не отправляйте персональные данные и сообщите администратору.", "error");
      sendToBackground({ type: "AIGATE_STATUS", ok: false });
    } else if (ok && warned) {
      warned = false;
      sendToBackground({ type: "AIGATE_STATUS", ok: true });
    }
  }, 3000);

  // Файлы расширение не проверяет: предупреждаем, чтобы это не было сюрпризом.
  document.addEventListener("change", (e) => {
    if (e.target instanceof HTMLInputElement && e.target.type === "file" && e.target.files.length) {
      notify("файлы не проверяются шлюзом. Не загружайте документы с персональными данными.", "info");
    }
  }, true);
  document.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      notify("файлы не проверяются шлюзом. Не загружайте документы с персональными данными.", "info");
    }
  }, true);

  // --- расшифровка меток ---------------------------------------------------------
  //
  // v0.3.2: настоящие значения БОЛЬШЕ НЕ ВПИСЫВАЮТСЯ в страницу сайта.
  // Раньше расширение заменяло [PERSON_1] на имя прямо в DOM ответа. ChatGPT
  // хранит диалог и у себя в браузере и синхронизирует его, а редактируемые
  // блоки ответа (карточка «Текст») воспринимают изменение DOM как правку
  // пользователя и сохраняют её на сервер. Так подставленные значения
  // уходили к OpenAI — уже после ответа, в обход маскировки.
  //
  // Теперь страница сайта не меняется вообще. Метки остаются метками, а их
  // значения показываются в собственной панели расширения: она живёт в
  // закрытом shadow DOM вне <body>, сайт её не видит и сохранить не может.

  let restoreTimer = null;

  function scheduleRestore() {
    clearTimeout(restoreTimer);
    restoreTimer = setTimeout(refreshPanel, 900);
  }

  // Список типов совпадает с EntityType шлюза (gateway/models.py)
  const TOKEN_RE_G =
    /\[(?:CONFIDENTIAL|BIRTHDATE|ADDRESS|PERSON|SECRET|AMOUNT|ID_DOC|PHONE|EMAIL|PLATE|IBAN|CARD|IIN|BIN)_\d+\]/g;

  const MESSAGE_SELECTORS = [
    '[data-message-author-role="assistant"]', // ChatGPT
    ".font-claude-message", // Claude
    '[data-is-streaming]', // Claude (новая разметка)
    "message-content", // Gemini
    "model-response",
  ];

  // Текст страницы без поля ввода. Токены могут быть разбиты на несколько
  // узлов разметки ("[" + "PERSON_1" + "]"), поэтому ищем в склеенном тексте.
  function visibleText() {
    const composer = findComposer();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let full = "";
    while (walker.nextNode()) {
      const n = walker.currentNode;
      if (!n.nodeValue) continue;
      if (composer && composer.contains(n)) continue;
      if (n.parentElement && n.parentElement.closest("script, style, noscript")) continue;
      full += n.nodeValue;
    }
    return full;
  }

  // Последний ответ ассистента. Разметка у сайтов разная и меняется: обычное
  // сообщение ChatGPT помечено data-message-author-role, а карточка «Текст»
  // (документ в ответе) — нет. Поэтому три попытки по убыванию точности.
  function lastAssistantText() {
    for (const sel of MESSAGE_SELECTORS) {
      const all = [...document.querySelectorAll(sel)].filter((el) => el.innerText.trim());
      if (all.length) return all[all.length - 1].innerText;
    }
    // ход диалога ChatGPT — <article>; ответ — последний ход без сообщения пользователя
    const turns = [...document.querySelectorAll("main article")].filter(
      (a) => !a.querySelector('[data-message-author-role="user"]') && a.innerText.trim()
    );
    if (turns.length) {
      // внутри хода — сам текст ответа, без заголовков и подписей кнопок
      const turn = turns[turns.length - 1];
      const body = [...turn.querySelectorAll('.markdown, [contenteditable="true"], .ProseMirror')]
        .filter((el) => el.innerText.trim());
      return (body.length ? body[body.length - 1] : turn).innerText;
    }
    // последний блок с меткой вне поля ввода и вне сообщений пользователя
    const composer = findComposer();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let last = null;
    while (walker.nextNode()) {
      const n = walker.currentNode;
      const el = n.parentElement;
      if (!el || (composer && composer.contains(n))) continue;
      if (el.closest('[data-message-author-role="user"]')) continue;
      if (/\[[A-Z_]+_\d+\]/.test(n.nodeValue)) last = el;
    }
    const block = last && last.closest("p, li, div, section");
    return block ? block.innerText : "";
  }

  async function restoreText(text) {
    const resp = await sendToBackground({ type: "AIGATE_RESTORE", convKey: convKey(), text });
    if (!resp.ok) {
      log("расшифровка не выполнена:", resp.error, "| ключ:", convKey());
      return null;
    }
    return resp.result.text;
  }

  // --- панель (shadow DOM вне <body>) ---

  let panel = null;

  function buildPanel() {
    const host = document.createElement("aigate-panel");
    host.style.cssText = "all:initial;position:fixed;right:16px;bottom:96px;z-index:2147483647";
    // вне <body>: ни React, ни редакторы сайта не наблюдают эту часть документа
    document.documentElement.appendChild(host);
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .box { font: 13px/1.45 system-ui, "Segoe UI", sans-serif; color: #1c2733; background: #fbfcfd;
               border: 1px solid #cfd6de; border-radius: 10px; box-shadow: 0 6px 20px rgba(28,39,51,.18);
               width: 300px; max-height: 50vh; display: flex; flex-direction: column; }
        header { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #e3e8ee;
                 cursor: pointer; user-select: none; }
        header b { font-weight: 650; flex: 1; }
        .count { color: #4b3f9e; font-weight: 600; }
        .list { overflow: auto; padding: 6px 10px; margin: 0; list-style: none; }
        .list li { display: flex; gap: 8px; padding: 3px 0; border-bottom: 1px dashed #e3e8ee; }
        .tok { color: #4b3f9e; background: #e6e3f6; border-radius: 3px; padding: 0 3px;
               font: 12px ui-monospace, Consolas, monospace; white-space: nowrap; align-self: flex-start; }
        .val { overflow-wrap: anywhere; }
        .actions { padding: 8px 10px; border-top: 1px solid #e3e8ee; display: flex; flex-direction: column; gap: 4px; }
        button { font: inherit; background: #4b3f9e; color: #fff; border: 0; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
        button:focus-visible { outline: 2px solid #4b3f9e; outline-offset: 2px; }
        .note { color: #5b6876; font-size: 12px; }
        .collapsed .list, .collapsed .actions { display: none; }
      </style>
      <div class="box">
        <header title="Свернуть или развернуть"><b>AI-Gate: настоящие значения</b><span class="count"></span></header>
        <ul class="list"></ul>
        <div class="actions">
          <button type="button" class="copy">Скопировать последний ответ с настоящими данными</button>
          <span class="note">Сайт видит только метки. Значения показаны здесь и только вам.</span>
        </div>
      </div>`;
    const box = root.querySelector(".box");
    root.querySelector("header").addEventListener("click", () => box.classList.toggle("collapsed"));
    root.querySelector(".copy").addEventListener("click", async () => {
      const text = lastAssistantText();
      if (!text) return notify("ответ не найден на странице", "info");
      const restored = await restoreText(text);
      if (restored == null) return notify("шлюз недоступен, расшифровать не удалось", "error");
      try {
        await navigator.clipboard.writeText(restored);
        notify("ответ с настоящими данными скопирован", "ok");
      } catch (_) {
        notify("браузер не дал доступ к буферу обмена", "error");
      }
    });
    return { host, list: root.querySelector(".list"), count: root.querySelector(".count") };
  }

  async function refreshPanel() {
    const tokens = [...new Set([...visibleText().matchAll(TOKEN_RE_G)].map((m) => m[0]))];
    if (!tokens.length) {
      if (panel) panel.host.style.display = "none";
      return;
    }
    const restored = await restoreText(tokens.join(SEP));
    if (restored == null) return;
    const values = restored.split(SEP);
    if (values.length !== tokens.length) return;
    const known = tokens
      .map((t, i) => [t, values[i]])
      .filter(([t, v]) => v && v !== t); // шлюз не знает токен: другой диалог или истёк срок
    if (!known.length) {
      if (panel) panel.host.style.display = "none";
      return;
    }
    if (!panel || !panel.host.isConnected) panel = buildPanel();
    panel.host.style.display = "block";
    panel.count.textContent = String(known.length);
    panel.list.replaceChildren(
      ...known.map(([t, v]) => {
        const li = document.createElement("li");
        const tok = document.createElement("span");
        tok.className = "tok";
        tok.textContent = t;
        const val = document.createElement("span");
        val.className = "val";
        val.textContent = v;
        li.append(tok, val);
        return li;
      })
    );
    log("расшифровано меток:", known.length);
  }

  new MutationObserver(scheduleRestore).observe(document.body, {
    childList: true, subtree: true, characterData: true,
  });
  scheduleRestore();
})();
