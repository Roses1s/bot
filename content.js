(async () => {
  // ─────────────────────────────────────────────
  // 1. Ждём появления ОГРН на странице (SPA!)
  // ─────────────────────────────────────────────
  const ogrnEl = await waitForElement('span[class*="ogrn-value"]', 15000);
  if (!ogrnEl) {
    console.warn('[ATI Finance] ОГРН не появился на странице');
    return;
  }

  const ogrn = ogrnEl.textContent.trim();
  if (!ogrn) return;

  // Строка ОГРН — ближайший родитель с классом row__
  const ogrnRow = ogrnEl.closest('div[class*="row__"]');
  if (!ogrnRow) {
    console.warn('[ATI Finance] Не найдена строка-обёртка ОГРН');
    return;
  }

  // Защита от повторной вставки (SPA-навигация)
  if (document.getElementById('afi-widget')) return;

  // ─────────────────────────────────────────────
  // 2. Создаём плашку и вставляем сразу под ОГРН
  // ─────────────────────────────────────────────
  injectStyles();
  const widget = document.createElement('div');
  widget.id = 'afi-widget';
  // Копируем класс строки ОГРН, чтобы выглядело нативно
  widget.className = ogrnRow.className;
  widget.innerHTML = `
    <div class="${getChildClass(ogrnRow, 'label__')}">Финансы</div>
    <div class="${getChildClass(ogrnRow, 'ogrn-container__') || getChildClass(ogrnRow, 'value__')}">
      <span class="afi-loading">⏳ загружаем данные с checko.ru…</span>
    </div>
  `;

  ogrnRow.insertAdjacentElement('afterend', widget);

  // ─────────────────────────────────────────────
  // 3. Запрос к checko.ru через background
  // ─────────────────────────────────────────────
  let html;
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'FETCH_CHECKO', ogrn });
    if (!resp?.success) throw new Error(resp?.error || 'Нет ответа');
    html = resp.html;
  } catch (err) {
    widget.querySelector('span').className = 'afi-error';
    widget.querySelector('span').textContent = `⚠️ ${err.message}`;
    return;
  }

  // ─────────────────────────────────────────────
  // 4. Парсим и рендерим
  // ─────────────────────────────────────────────
  const data = parseCheckoHTML(html);
  if (!data) {
    widget.querySelector('span').className = 'afi-error';
    widget.querySelector('span').textContent = '⚠️ Финансовые данные не найдены';
    return;
  }

  renderWidget(widget, data, ogrn);
})();


// ─────────────────────────────────────────────────────────
// ОЖИДАНИЕ ЭЛЕМЕНТА (для SPA)
// ─────────────────────────────────────────────────────────

function waitForElement(selector, timeout = 10000) {
  return new Promise((resolve) => {
    // Уже есть?
    const existing = document.querySelector(selector);
    if (existing) return resolve(existing);

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) {
        observer.disconnect();
        resolve(el);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Таймаут — отдаём null
    setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeout);
  });
}


// ─────────────────────────────────────────────────────────
// УТИЛИТА: класс дочернего элемента по префиксу
// ─────────────────────────────────────────────────────────

function getChildClass(parentEl, classPrefix) {
  const child = parentEl.querySelector(`[class*="${classPrefix}"]`);
  return child ? child.className : '';
}


// ─────────────────────────────────────────────────────────
// ПАРСИНГ HTML CHECKO.RU
// ─────────────────────────────────────────────────────────

function parseCheckoHTML(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');

  let financeBlock = null;
  for (const block of doc.querySelectorAll('.mb-3')) {
    if (block.querySelector('.fw-700')?.textContent.includes('Финансовая отчетность')) {
      financeBlock = block;
      break;
    }
  }
  if (!financeBlock) return null;

  const year = financeBlock.querySelector('.fw-700').textContent.match(/\d{4}/)?.[0] ?? '';
  const result = { year, revenue: null, netProfit: null };

  for (const row of financeBlock.querySelectorAll(':scope > div')) {
    const link = row.querySelector('a.link-pseudo');
    if (!link) continue;
    const label = link.textContent.trim();

    if (label === 'Выручка')             result.revenue   = extractMetric(row);
    else if (label === 'Чистая прибыль') result.netProfit  = extractMetric(row);

    if (result.revenue && result.netProfit) break;
  }

  return result;
}

function extractMetric(rowEl) {
  // Значение — последний непустой текстовый узел
  let value = '';
  for (const node of rowEl.childNodes) {
    if (node.nodeType === Node.TEXT_NODE && node.textContent.trim())
      value = node.textContent.trim();
  }

  // Изменение в % + предыдущий год
  const changeSpan = rowEl.querySelector('[data-bs-title]');
  return {
    value,
    change:    changeSpan?.textContent.trim() ?? '',
    prevLabel: changeSpan?.getAttribute('data-bs-title') ?? ''
  };
}


// ─────────────────────────────────────────────────────────
// РЕНДЕРИНГ ВИДЖЕТА
// ─────────────────────────────────────────────────────────

function renderWidget(widget, data, ogrn) {
  const { year, revenue, netProfit } = data;

  const fmt = (m) => {
    if (!m) return '<span class="afi-empty">нет данных</span>';
    const cls = m.change.startsWith('+') ? 'afi-pos' : 'afi-neg';
    return `
      <span class="afi-val">${m.value}</span>
      <span class="${cls}">${m.change}</span>
      ${m.prevLabel ? `<span class="afi-prev" title="${m.prevLabel}">ℹ️</span>` : ''}
    `;
  };

  // Вторая колонка виджета (value-часть)
  const valueCol = widget.children[1];
  valueCol.innerHTML = `
    <div class="afi-wrap">
      <span class="afi-year">📊 ${year}:</span>
      <span class="afi-item">
        <span class="afi-name">Выручка</span>${fmt(revenue)}
      </span>
      <span class="afi-sep">•</span>
      <span class="afi-item">
        <span class="afi-name">Прибыль</span>${fmt(netProfit)}
      </span>
      <a class="afi-link" href="https://checko.ru/company/${ogrn}" target="_blank" rel="noopener">
        checko.ru ↗
      </a>
    </div>
  `;
}


// ─────────────────────────────────────────────────────────
// СТИЛИ
// ─────────────────────────────────────────────────────────

function injectStyles() {
  if (document.getElementById('afi-styles')) return;
  const s = document.createElement('style');
  s.id = 'afi-styles';
  s.textContent = `
    #afi-widget {
      align-items: center !important;
    }

    #afi-widget .afi-wrap {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      padding: 2px 0;
    }

    #afi-widget .afi-year  { font-weight: 600; color: #555; }
    #afi-widget .afi-item  { display: inline-flex; align-items: center; gap: 3px; }
    #afi-widget .afi-name  { color: #777; }
    #afi-widget .afi-val   { font-weight: 600; color: #222; }
    #afi-widget .afi-pos   { color: #1a7f3c; font-size: 11px; }
    #afi-widget .afi-neg   { color: #c0392b; font-size: 11px; }
    #afi-widget .afi-sep   { color: #ccc; }

    #afi-widget .afi-prev {
      cursor: default;
      font-size: 12px;
      color: #aaa;
      position: relative;
    }
    #afi-widget .afi-prev:hover::after {
      content: attr(title);
      position: absolute;
      left: 18px; top: -6px;
      background: #333; color: #fff;
      padding: 3px 8px; border-radius: 4px;
      font-size: 11px; white-space: nowrap;
      z-index: 9999;
    }

    #afi-widget .afi-link {
      font-size: 11px;
      color: #1a5fa8;
      text-decoration: none;
      margin-left: 4px;
    }
    #afi-widget .afi-link:hover { text-decoration: underline; }

    #afi-widget .afi-loading { color: #999; font-size: 13px; }
    #afi-widget .afi-error   { color: #c0392b; font-size: 13px; }
    #afi-widget .afi-empty   { color: #aaa; font-size: 13px; }
  `;
  document.head.appendChild(s);
}