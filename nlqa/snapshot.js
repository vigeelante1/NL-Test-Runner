// Builds a compact, numbered description of what a user can see and interact with.
// Every interactive element gets data-nlqa-id so the AI can say "click 12" and we can find it again.
// Runs once per frame; `startId` keeps ids unique across frames.
(startId) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  document.querySelectorAll('[data-nlqa-id]').forEach(e => e.removeAttribute('data-nlqa-id'));

  const isVisible = e => {
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };

  const SELECTOR = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]', '[role=checkbox]',
    '[role=radio]', '[role=combobox]', '[role=option]', '[role=switch]', '[onclick]',
    '[contenteditable=""]', '[contenteditable=true]'
  ].join(',');

  // Collect elements, including inside open shadow roots.
  const found = [];
  const visit = root => {
    root.querySelectorAll(SELECTOR).forEach(e => found.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) visit(e.shadowRoot); });
  };
  visit(document);

  const labelText = e => {
    if (e.labels && e.labels.length) return clean([...e.labels].map(l => l.innerText).join(' '));
    const by = e.getAttribute('aria-labelledby');
    if (by) return clean(by.split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' '));
    return '';
  };

  const nameOf = e => {
    const tag = e.tagName.toLowerCase();
    const candidates = [
      e.getAttribute('aria-label'), labelText(e),
      (tag === 'input' || tag === 'textarea' || tag === 'select') ? '' : e.innerText,
      e.getAttribute('placeholder'), e.getAttribute('title'),
      [...e.querySelectorAll('img[alt]')].map(i => i.alt).join(' '),
      (tag === 'input' && ['submit', 'button', 'reset'].includes(e.type)) ? e.value : '',
      e.getAttribute('name')
    ];
    return cut(clean(candidates.find(c => clean(c)) || ''), 80);
  };

  const roleOf = e => {
    const r = e.getAttribute('role');
    if (r) return r;
    const tag = e.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'input') return ['checkbox', 'radio', 'submit', 'button', 'file', 'range'].includes(e.type)
      ? (e.type === 'submit' ? 'button' : e.type) : 'textbox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'dropdown';
    return tag;
  };

  const lines = [];
  let id = startId;
  const seen = new Set();
  for (const e of found) {
    if (seen.has(e) || !isVisible(e)) continue;
    seen.add(e);
    // skip links/buttons nested inside another collected element with the same text (duplicates)
    const role = roleOf(e);
    const name = nameOf(e);
    const extras = [];
    const tag = e.tagName.toLowerCase();
    if (tag === 'input' && e.type && !['text', 'submit', 'button', 'checkbox', 'radio'].includes(e.type)) extras.push(`type=${e.type}`);
    if (e.getAttribute('placeholder') && e.getAttribute('placeholder') !== name) extras.push(`placeholder="${cut(clean(e.getAttribute('placeholder')), 40)}"`);
    if ((tag === 'input' || tag === 'textarea') && !['checkbox', 'radio', 'submit', 'button'].includes(e.type) && e.value)
      extras.push(e.type === 'password' ? 'value="••••"' : `value="${cut(clean(e.value), 40)}"`);
    if (tag === 'select') {
      const opts = [...e.options].map(o => clean(o.text)).filter(Boolean);
      extras.push(`selected="${cut(clean(e.selectedOptions[0] ? e.selectedOptions[0].text : ''), 30)}"`);
      extras.push(`options=[${cut(opts.slice(0, 12).join(' | '), 160)}${opts.length > 12 ? ' | …' : ''}]`);
    }
    if (e.checked) extras.push('checked');
    if (e.disabled) extras.push('disabled');
    if (tag === 'a') {
      const href = e.getAttribute('href') || '';
      if (href && !href.startsWith('javascript')) extras.push(`href=${cut(href, 60)}`);
      if (e.target === '_blank') extras.push('opens-new-tab');
    }
    const r = e.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight) extras.push('offscreen');

    e.setAttribute('data-nlqa-id', String(id));
    lines.push(`[${id}] ${role} "${name}"${extras.length ? ' ' + extras.join(' ') : ''}`);
    id++;
    if (lines.length >= 250) { lines.push('… (more elements omitted)'); break; }
  }

  const headings = [...document.querySelectorAll('h1,h2,h3')].filter(isVisible)
    .map(h => clean(h.innerText)).filter(Boolean).slice(0, 12);

  return {
    nextId: id,
    elements: lines,
    headings,
    text: clean(document.body ? document.body.innerText : '')
  };
}
