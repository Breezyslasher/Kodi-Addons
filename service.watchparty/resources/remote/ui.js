// The Android app's components (watchparty-tv Ui.kt) as DOM elements.

/** el('div', {class: 'x', onclick: f}, child, 'text', …) */
export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) e.append(c);
  return e;
}

export const label = text => el('div', { class: 'label' }, text);

/** A picture, or the striped placeholder. */
export function art(src) {
  const box = el('div', { class: 'art' });
  if (src) {
    const img = el('img', { src, alt: '', loading: 'lazy' });
    img.addEventListener('error', () => img.remove());
    box.append(img);
  }
  return box;
}

export function posterCard(title, meta, image, onClick) {
  return el('button', { class: 'poster', onclick: onClick, title },
    art(image), el('span', { class: 'ptitle' }, title), meta ? el('span', { class: 'pmeta' }, meta) : null);
}

export function serviceCard(name, note, logo, onClick) {
  const badge = el('div', { class: 'logo' });
  if (logo) {
    const img = el('img', { src: logo, alt: '' });
    img.addEventListener('error', () => { img.remove(); badge.textContent = name.slice(0, 1); });
    badge.append(img);
  } else {
    badge.textContent = name.slice(0, 1);
  }
  return el('button', { class: 'service', onclick: onClick }, badge, el('div', {}, el('b', {}, name), el('span', {}, note)));
}

export function listButton(text, note, onClick) {
  return el('button', { class: 'listButton', onclick: onClick }, el('span', {}, text), note ? el('small', {}, note) : null);
}

export function rating(score, source) {
  return el('span', { class: 'rating' }, el('b', {}, score.toFixed(1)), el('span', {}, source));
}

/** "1999-03-31" → "31 Mar 1999" (the reader's way). */
export function date(s) {
  if (!s) return null;
  const d = new Date(s + 'T00:00:00');
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

let toastTimer = 0;
export function toast(text) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}
