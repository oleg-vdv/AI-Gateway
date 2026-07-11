// AI-Gate DLP — content script для веб-интерфейсов ChatGPT / Claude / Gemini.
//
// Принцип (F1, канал «Браузер»): перехват отправки в capture-фазе ДО того,
// как страница обработает Enter/клик по кнопке отправки. Текст уходит на
// шлюз (/v1/check, dry_run), поле ввода заменяется обезличенным текстом,
// после чего отправка повторяется программно. Ответ ассистента
// детокенизируется через /v1/restore и подменяется в DOM.
//
// Fail-closed: если шлюз недоступен или вернул block/fail_closed —
// отправка НЕ происходит, пользователь видит уведомление.

(() => {
  "use strict";

  const MARK = "data-aigate-approved";
  let currentSessionId = null;

  // --- поиск поля ввода/кнопки на поддерживаемых сайтах ---------------------

  function findComposer() {
    return (
      document.querySelector("#prompt-textarea") || // ChatGPT
      document.querySelector('div[contenteditable="true"].ProseMirror') || // Claude
      document.querySelector('div[contenteditable="true"][role="textbox"]') || // Gemini/прочие
      document.querySelector("main textarea") ||
      document.querySelector("textarea")
    );
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
      // execCommand деприкейтнут, но остаётся единственным способом вставки,
      // которую редакторы React/ProseMirror видят как пользовательский ввод
      document.execCommand("insertText", false, text);
    }
  }

  // --- уведомления -----------------------------------------------------------

  function notify(text, kind) {
    let el = document.getElementById("aigate-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "aigate-toast";
      el.style.cssText =
        "position:fixed;top:12px;right:12px;z-index:99999;padding:10px 16px;" +
        "border-radius:10px;font:13px system-ui;color:#fff;max-width:360px;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.25);white-space:pre-wrap";
      document.body.appendChild(el);
    }
    el.style.background =
      kind === "error" ? "#dc2626" : kind === "ok" ? "#16a34a" : "#2563eb";
    el.textContent = "AI-Gate: " + text;
    el.style.display = "block";
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.style.display = "none"), 6000);
  }

  function sendToBackground(msg) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage(msg, (resp) =>
        resolve(resp || { ok: false, error: "нет ответа от service worker" })
      )
    );
  }

  // --- основной перехват -------------------------------------------------------

  let processing = false;

  async function interceptAndResubmit(composer, resubmit) {
    const original = composerText(composer).trim();
    if (!original) return;
    processing = true;
    try {
      const resp = await sendToBackground({
        type: "AIGATE_CHECK",
        text: original,
        sessionId: null,
      });
      if (!resp.ok) {
        notify("шлюз недоступен — отправка заблокирована (fail-closed).\n" + resp.error, "error");
        return;
      }
      const r = resp.result;
      if (r.verdict === "blocked" || r.verdict === "fail_closed") {
        notify(r.message || "запрос заблокирован политикой", "error");
        return;
      }
      currentSessionId = r.session_id;
      const counts = Object.entries(r.entity_counts || {});
      if (r.verdict === "masked" && r.masked_text != null) {
        setComposerText(composer, r.masked_text);
        notify(
          "замаскировано: " + counts.map(([t, c]) => `${t}×${c}`).join(", "),
          "ok"
        );
        watchForAnswer();
      }
      composer.setAttribute(MARK, "1");
      resubmit();
      setTimeout(() => composer.removeAttribute(MARK), 500);
    } finally {
      processing = false;
    }
  }

  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      const composer = findComposer();
      if (!composer || !composer.contains(e.target)) return;
      if (composer.getAttribute(MARK) || processing) return;
      const text = composerText(composer);
      if (!text.trim()) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      interceptAndResubmit(composer, () => {
        composer.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true,
          })
        );
      });
    },
    true
  );

  document.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest(
        'button[data-testid="send-button"], button[aria-label*="Send"], ' +
        'button[aria-label*="Отправить"], button[aria-label*="Submit"]'
      );
      if (!btn) return;
      const composer = findComposer();
      if (!composer || composer.getAttribute(MARK) || processing) return;
      if (!composerText(composer).trim()) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      interceptAndResubmit(composer, () => btn.click());
    },
    true
  );

  // --- детокенизация ответа ассистента ----------------------------------------

  const TOKEN_RE = /\[(?:IIN|BIN|PERSON|IBAN|CARD|SECRET|AMOUNT)_\d+\]/;
  let watching = false;

  function watchForAnswer() {
    if (watching || !currentSessionId) return;
    watching = true;
    let idleTimer = null;
    const observer = new MutationObserver(() => {
      clearTimeout(idleTimer);
      // ждём паузу в стриминге, затем детокенизируем разом
      idleTimer = setTimeout(restoreVisibleTokens, 1200);
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    setTimeout(() => { observer.disconnect(); watching = false; }, 120000);

    async function restoreVisibleTokens() {
      if (!currentSessionId) return;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) {
        if (TOKEN_RE.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
      }
      if (!nodes.length) return;
      const joined = nodes.map((n) => n.nodeValue);
      const resp = await sendToBackground({
        type: "AIGATE_RESTORE",
        sessionId: currentSessionId,
        text: JSON.stringify(joined),
        purge: false,
      });
      if (!resp.ok) return;
      try {
        const restored = JSON.parse(resp.result.text);
        nodes.forEach((n, i) => { if (restored[i]) n.nodeValue = restored[i]; });
      } catch (_) { /* ответ ещё стримится — попробуем на следующей паузе */ }
    }
  }
})();
