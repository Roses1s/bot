/**
 * Слушаем запросы от content.js.
 * Делаем fetch checko.ru из background, чтобы обойти CORS.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== 'FETCH_CHECKO') return;

  fetch(`https://checko.ru/company/${message.ogrn}`, {
    headers: {
      // Имитируем обычный браузер
      'User-Agent': navigator.userAgent,
      'Accept': 'text/html'
    }
  })
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.text();
    })
    .then(html => sendResponse({ success: true, html }))
    .catch(err => sendResponse({ success: false, error: err.message }));

  return true; // Обязательно для асинхронного sendResponse
});