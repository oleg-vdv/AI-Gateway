// AI-Gate DLP — content script для ChatGPT / Claude / Gemini.
//
// Перехват отправки в capture-фазе ДО того, как страница обработает Enter
// или клик по кнопке. Текст уходит на шлюз (/v1/check, dry_run), поле ввода
// заменяется обезличенным текстом, после чего отправка повторяется.
// Ответ ассистента детокенизируется через /v1/restore прямо в DOM.
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

  // ChatGPT сначала даёт диалогу временный адрес /c/WEB:<uuid>, а после
  // ответа сервера заменяет его настоящим /c/<id>. Временный адрес всегда
  // передаёт сессию дальше, сколько бы времени ни прошло.
  const TEMP_PATH_RE = /\/c\/WEB:/i;

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

  async function resubmit(composer, clickedButton) {
    composer.setAttribute(MARK, "1");
    await nextFrame(); // даём React включить кнопку после смены текста
    const btn = clickedButton || findSendButton();
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

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    const composer = findComposer();
    if (!composer || !composer.contains(e.target)) return;
    if (composer.getAttribute(MARK)) return;
    if (processing) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (!composerText(composer).trim()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    interceptAndResubmit(composer, null);
  }, true);

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(SEND_SELECTORS.join(","));
    if (!btn) return;
    const composer = findComposer();
    if (!composer || composer.getAttribute(MARK)) return;
    if (processing) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (!composerText(composer).trim()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    interceptAndResubmit(composer, btn);
  }, true);

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

  // --- детокенизация ответов -------------------------------------------------------
  // Наблюдатель работает всё время, пока открыта страница: сайты перерисовывают
  // сообщения (React), и восстановленный текст может вернуться к токенам.

  let restoreTimer = null;

  function scheduleRestore() {
    clearTimeout(restoreTimer);
    restoreTimer = setTimeout(restoreVisibleTokens, 900);
  }

  const TOKEN_RE_G = /\[(?:IIN|BIN|PERSON|IBAN|CARD|SECRET|AMOUNT)_\d+\]/g;

  function shouldSkip(node) {
    const parent = node.parentElement;
    if (!parent) return true;
    if (parent.closest("#aigate-toast, script, style, noscript")) return true;
    // Реальные данные никогда не возвращаются в поле ввода сообщения.
    // Остальные редактируемые блоки (например, карточка-черновик ChatGPT)
    // восстанавливаем: это ответ модели, а не то, что уйдёт наружу.
    const composer = findComposer();
    if (composer && composer.contains(node)) return true;
    return false;
  }

  // Сайты могут разбить токен на несколько узлов разметки:
  // "[" + "PERSON_1" + "]" в разных <span>. Поэтому ищем токены в склеенном
  // тексте и затем раскладываем замену обратно по исходным узлам.
  async function restoreVisibleTokens() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    const starts = [];
    let full = "";
    while (walker.nextNode()) {
      const n = walker.currentNode;
      if (!n.nodeValue || shouldSkip(n)) continue;
      nodes.push(n);
      starts.push(full.length);
      full += n.nodeValue;
    }
    const matches = [...full.matchAll(TOKEN_RE_G)];
    if (!matches.length) return;

    const unique = [...new Set(matches.map((m) => m[0]))];
    const resp = await sendToBackground({
      type: "AIGATE_RESTORE",
      convKey: convKey(),
      text: unique.join(SEP),
    });
    if (!resp.ok) {
      log("восстановление не выполнено:", resp.error, "| ключ:", convKey(), "| токены:", unique.join(" "));
      return;
    }
    const restored = resp.result.text.split(SEP);
    if (restored.length !== unique.length) return;
    log("восстановлено токенов:", unique.length);
    const value = new Map(unique.map((t, i) => [t, restored[i]]));

    // индекс узла, в который попадает позиция pos склеенного текста
    function locate(pos) {
      let lo = 0, hi = starts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (starts[mid] <= pos) lo = mid; else hi = mid - 1;
      }
      return lo;
    }

    // с конца, чтобы замены не сдвигали ещё не обработанные позиции
    for (const m of matches.reverse()) {
      const real = value.get(m[0]);
      if (!real || real === m[0]) continue; // шлюз не знает токен (истёк TTL)
      const a = locate(m.index);
      const b = locate(m.index + m[0].length - 1);
      const na = nodes[a], nb = nodes[b];
      if (!na.isConnected || !nb.isConnected) continue;
      const offA = m.index - starts[a];
      const offB = m.index + m[0].length - starts[b];
      if (a === b) {
        na.nodeValue = na.nodeValue.slice(0, offA) + real + na.nodeValue.slice(offB);
      } else {
        const tail = nb.nodeValue.slice(offB);
        na.nodeValue = na.nodeValue.slice(0, offA) + real;
        for (let i = a + 1; i < b; i++) nodes[i].nodeValue = "";
        nb.nodeValue = tail;
      }
    }
  }

  new MutationObserver(scheduleRestore).observe(document.body, {
    childList: true, subtree: true, characterData: true,
  });
  scheduleRestore();
})();
