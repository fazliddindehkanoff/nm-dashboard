(() => {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  const supports = version => Boolean(tg && (!tg.isVersionAtLeast || tg.isVersionAtLeast(version)));
  if (tg) {
    tg.ready();
    tg.expand();
    if (supports('6.1')) {
      tg.setHeaderColor('#00213D');
      tg.setBackgroundColor('#F4F7F9');
    }
  }
  const demo = document.body.dataset.demo === '1';

  const state = {
    profile: null,
    courses: [],
    myCourses: [],
    purchases: [],
    course: null,
    groupId: null,
    type: 'self',
    payMode: null,
    purchase: null,
    checkoutFromCourse: false,
    contract: null,
    legal: null,
    back: null,
    answers: {},
  };

  // ---- Helpers

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const amount = value => String(Math.round(Number(value || 0))).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const money = value => `${amount(value)} so‘m`;
  const months = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'];
  const monthsShort = ['yan', 'fev', 'mar', 'apr', 'may', 'iyun', 'iyul', 'avg', 'sen', 'okt', 'noy', 'dek'];
  const weekdays = ['yakshanba', 'dushanba', 'seshanba', 'chorshanba', 'payshanba', 'juma', 'shanba'];
  const weekdaysShort = ['yak', 'dush', 'sesh', 'chor', 'pay', 'juma', 'shan'];
  const dateParts = value => {
    const [year, month, day] = value.slice(0, 10).split('-').map(Number);
    return { year, month, day, weekday: new Date(Date.UTC(year, month - 1, day)).getUTCDay() };
  };
  const formatDate = value => {
    if (!value) return 'Sana belgilanmagan';
    const { year, month, day } = dateParts(value);
    return `${day}-${months[month - 1]}${year !== new Date().getFullYear() ? ` ${year}` : ''}`;
  };
  const formatLongDate = value => {
    const text = `${weekdays[dateParts(value).weekday]}, ${formatDate(value)}`;
    return text[0].toUpperCase() + text.slice(1);
  };
  const formatDateTime = value => {
    if (!value) return '';
    const parsed = new Date(value);
    return `${parsed.getDate()}-${months[parsed.getMonth()]}, ${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`;
  };
  const daysUntil = value => {
    const { year, month, day } = dateParts(value);
    const today = new Date();
    return Math.round((Date.UTC(year, month - 1, day) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
  };
  const initials = name => (name || 'N').split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const csrf = $('meta[name="csrf-token"]').content;
  // Keep names like «Intuitsiya 3-kurs» from breaking after the hyphen.
  const titleText = value => String(value ?? '').replace(/(\S)-(\S)/g, '$1\u2011$2');

  function headers(json = false) {
    const result = { 'X-Telegram-Init-Data': tg?.initData || '', 'X-CSRFToken': csrf };
    if (demo) result['X-Telegram-Demo'] = '1';
    if (json) result['Content-Type'] = 'application/json';
    return result;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { ...headers(typeof options.body === 'string'), ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({ ok: false, error: 'Server javobini o‘qib bo‘lmadi. Qayta urinib ko‘ring.' }));
    if (!response.ok || !data.ok) throw new Error(data.error || 'Amal bajarilmadi. Qayta urinib ko‘ring.');
    return data;
  }

  let toastTimer;
  function toast(message, error = false) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.toggle('is-error', error);
    node.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('is-visible'), 3400);
  }

  function confirmAction(message) {
    if (tg?.showConfirm && supports('6.2')) return new Promise(resolve => tg.showConfirm(message, resolve));
    return Promise.resolve(window.confirm(message));
  }

  function haptic() {
    if (supports('6.1')) tg.HapticFeedback?.selectionChanged();
  }

  // ---- Navigation: three tabs; every other screen has a way back

  const TAB_OF_VIEW = { catalogView: 'catalog', profileView: 'profile' };

  function showView(id, back = null) {
    $$('.view').forEach(node => node.classList.toggle('is-active', node.id === id));
    const tab = TAB_OF_VIEW[id];
    $('.tabbar').hidden = !tab;
    document.body.classList.toggle('no-tabbar', !tab);
    $$('.tabbar [data-tab]').forEach(button => {
      const active = button.dataset.tab === tab;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    state.back = back;
    if (tg?.BackButton && supports('6.1')) {
      if (back) tg.BackButton.show(); else tg.BackButton.hide();
    }
    window.scrollTo(0, 0);
  }

  function goBack() { if (state.back) state.back(); }

  function showCatalog() { renderCatalog(); showView('catalogView'); }
  // The profile also lists purchased courses and unfinished orders.
  function showProfile() { renderTodos(); renderMyCourses(); showView('profileView'); }

  // ---- Covers: an uploaded group photo, otherwise the brand's three circles

  const openGroups = course => course.active_groups.filter(group => group.can_purchase);
  const coverUrl = course => (openGroups(course).find(group => group.banner_url) || course.active_groups.find(group => group.banner_url) || {}).banner_url || '';
  const motif = seed => `<span class="motif motif--${Math.abs(Number(seed) || 0) % 4}" aria-hidden="true"><svg viewBox="0 0 120 112"><circle cx="60" cy="29" r="22"/><circle cx="34" cy="79" r="22"/><circle cx="86" cy="79" r="22"/></svg></span>`;
  const coverHtml = (seed, url, eager = false) => (url ? `<img src="${escapeHtml(url)}" alt="" loading="${eager ? 'eager' : 'lazy'}" decoding="async" data-seed="${seed}">` : motif(seed));

  function bindCovers(root) {
    $$('img[data-seed]', root).forEach(image => image.addEventListener('error', () => {
      image.outerHTML = motif(image.dataset.seed);
    }, { once: true }));
  }

  const stubParts = value => {
    const { month, day, weekday } = dateParts(value);
    return `<span class="stub__month">${monthsShort[month - 1]}</span><span class="stub__day">${day}</span><span class="stub__week">${weekdaysShort[weekday]}</span>`;
  };

  function familyOffer(course, short = false) {
    const rule = [...(course.participant_discounts || [])].sort((a, b) => a.min_participants - b.min_participants)[0];
    if (!rule) return '';
    return short
      ? `${rule.min_participants}+ kishi: har biriga −${money(rule.amount)}`
      : `${rule.min_participants} va undan ortiq kishiga har biriga −${money(rule.amount)}`;
  }

  // ---- Catalogue

  function sortedCourses() {
    return [...state.courses].sort((a, b) => {
      const first = openGroups(a)[0];
      const second = openGroups(b)[0];
      if (Boolean(first) !== Boolean(second)) return first ? -1 : 1;
      if (first && second) return first.start_date.localeCompare(second.start_date) || a.name.localeCompare(b.name);
      return a.name.localeCompare(b.name);
    });
  }

  function renderCatalog() {
    const host = $('#catalog');
    if (!state.courses.length) {
      host.innerHTML = '<li class="empty"><h3>Yangi guruhlar tez orada</h3><p>Guruh ochilishi bilan kurs shu yerda paydo bo‘ladi.</p></li>';
      return;
    }
    host.innerHTML = sortedCourses().map(course => {
      const groups = openGroups(course);
      const next = groups[0];
      const offer = next ? familyOffer(course, true) : '';
      const when = next ? (groups.length > 1 ? `${groups.length} ta guruh` : formatLongDate(next.start_date)) : 'yangi guruh tez orada';
      const label = `${course.name}. ${next ? `Eng yaqin guruh: ${formatDate(next.start_date)}.` : 'Hozircha qabul yopiq.'} Bir kishi uchun ${money(course.price)}.`;
      return `<li><button class="course-card${next ? '' : ' is-closed'}" type="button" data-course="${course.id}" aria-label="${escapeHtml(label)}">
        <span class="course-card__media"><span class="course-card__cover">${coverHtml(course.id, coverUrl(course))}</span>${next ? `<span class="stub" aria-hidden="true">${stubParts(next.start_date)}</span>` : ''}</span>
        <span class="course-card__body">
          <span class="course-card__title">${escapeHtml(titleText(course.name))}</span>
          <span class="course-card__meta">${course.number_of_days} kunlik kurs · ${when}</span>
          ${offer ? `<span class="tag tag--offer">${escapeHtml(offer)}</span>` : ''}
          <span class="course-card__foot"><span class="price"><b>${amount(course.price)}</b> so‘m<small>bir kishi uchun</small></span><span class="course-card__cta">${next ? 'Sotib olish' : 'Qabul yopiq'}</span></span>
        </span>
      </button></li>`;
    }).join('');
    $$('[data-course]', host).forEach(button => button.addEventListener('click', () => openCourse(Number(button.dataset.course))));
    bindCovers(host);
  }

  // ---- Unfinished orders: unpaid, partly paid or missing the questionnaire

  const isUnpaid = purchase => ['pending', 'failed'].includes(purchase.payment_status) && !Number(purchase.paid_amount);
  const todoPurchases = () => state.purchases.filter(purchase => purchase.payment_status !== 'refunded'
    && (purchase.payment_status !== 'success' || !purchase.questionnaire_completed));

  function renderTodoIndicators() {
    const todos = todoPurchases();
    $('#todoBadge').hidden = !todos.length;
    $('#todoBadge').textContent = todos.length;
    $('#todoBanner').hidden = !todos.length;
    if (!todos.length) return;
    if (todos.length === 1) {
      const [purchase] = todos;
      $('#todoBannerTitle').textContent = isUnpaid(purchase) ? 'To‘lov kutilmoqda' : !purchase.questionnaire_completed && Number(purchase.paid_amount) ? 'Anketani to‘ldiring' : 'Qolgan to‘lov bor';
      $('#todoBannerText').textContent = purchase.course;
    } else {
      $('#todoBannerTitle').textContent = `${todos.length} ta buyurtma yakunlanmagan`;
      $('#todoBannerText').textContent = 'Profil bo‘limida davom ettiring';
    }
  }

  function todoCard(purchase) {
    const group = purchase.group ? `${formatDate(purchase.group.start_date)} · ` : '';
    const people = `${purchase.participant_count} kishi`;
    const pay = label => `<button type="button" class="btn btn--primary" data-pay="${purchase.id}">${label}</button>`;
    const questionnaire = label => `<button type="button" class="btn btn--primary" data-questionnaire="${purchase.id}">${label}</button>`;
    const cancel = purchase.can_cancel ? `<button type="button" class="text-button text-button--danger" data-cancel="${purchase.id}">Bekor qilish</button>` : '';
    let kicker; let text; let actions;
    if (isUnpaid(purchase)) {
      kicker = purchase.payment_status === 'failed' ? 'To‘lov o‘tmadi' : 'To‘lov kutilmoqda';
      text = `${group}${people} · ${money(purchase.sale_amount)}`;
      actions = pay('To‘lash') + cancel;
    } else if (!purchase.questionnaire_completed) {
      kicker = 'Anketa to‘ldirilmagan';
      text = Number(purchase.payable_amount) > 0
        ? `${group}to‘langan ${money(purchase.paid_amount)} · qolgan ${money(purchase.payable_amount)}`
        : `${group}${people} · to‘lov qabul qilingan`;
      actions = questionnaire('Anketani to‘ldirish')
        + (Number(purchase.payable_amount) > 0 ? `<button type="button" class="text-button" data-pay="${purchase.id}">Qolganini to‘lash</button>` : '');
    } else {
      kicker = 'Qolgan to‘lov';
      text = `${group}to‘langan ${money(purchase.paid_amount)} · qolgan ${money(purchase.payable_amount)}`;
      actions = pay('To‘lash');
    }
    return `<article class="todo"><p class="todo__kicker">${kicker}</p><h3>${escapeHtml(purchase.course)}</h3><p>${text}</p><div class="todo__actions">${actions}</div></article>`;
  }

  function renderTodos() {
    const host = $('#todoList');
    const todos = todoPurchases();
    host.innerHTML = todos.length ? `<p class="section-label">Yakunlanmagan buyurtmalar</p>${todos.map(todoCard).join('')}` : '';
    $$('[data-pay]', host).forEach(button => button.addEventListener('click', () => {
      const purchase = state.purchases.find(item => item.id === Number(button.dataset.pay));
      if (purchase) openCheckout(purchase);
    }));
    $$('[data-questionnaire]', host).forEach(button => button.addEventListener('click', () => {
      const purchase = state.purchases.find(item => item.id === Number(button.dataset.questionnaire));
      if (purchase) openQuestionnaire(purchase);
    }));
    $$('[data-cancel]', host).forEach(button => button.addEventListener('click', () => cancelPurchase(Number(button.dataset.cancel))));
  }

  // ---- Course page: date, people and payment in one place

  function openCourse(courseId) {
    const course = state.courses.find(item => item.id === courseId);
    if (!course) return;
    const groups = openGroups(course);
    state.course = course;
    state.groupId = groups[0]?.id ?? null;
    state.payMode = null;
    $('#courseKicker').textContent = `${course.number_of_days} kunlik kurs`;
    $('#courseTitle').textContent = titleText(course.name);
    $('#coursePrice').innerHTML = `<b>${money(course.price)}</b> · bir kishi uchun`;
    const offer = familyOffer(course);
    $('#courseOffer').hidden = !offer || !groups.length;
    $('#courseOffer').textContent = offer;

    const unpaid = state.purchases.find(item => item.course_id === course.id && isUnpaid(item));
    const resume = $('#resumeNote');
    resume.hidden = !unpaid;
    if (unpaid) {
      resume.innerHTML = '<p>Bu kurs uchun to‘lanmagan buyurtmangiz bor.</p><button type="button">To‘lovga o‘tish</button>';
      $('button', resume).addEventListener('click', () => openCheckout(unpaid));
    }

    renderSelf();
    $('#familyMembers').innerHTML = '';
    delete $('#courseCover').dataset.url;
    renderGroups();
    setPurchaseType('self');
    showView('courseView', showCatalog);
  }

  function setCourseCover(url) {
    const host = $('#courseCover');
    if (host.dataset.url === url && host.childElementCount) return;
    host.dataset.url = url;
    host.innerHTML = coverHtml(state.course.id, url, true);
    bindCovers(host);
  }

  function renderGroups() {
    const groups = openGroups(state.course);
    const host = $('#groupStubs');
    $('#groupCount').textContent = groups.length > 1 ? `${groups.length} ta guruh` : '';
    $('#buyButton').disabled = !groups.length;
    if (!groups.length) {
      host.innerHTML = '';
      $('#groupDetail').innerHTML = '<strong>Hozircha qabul yopiq.</strong><span>Yangi guruh ochilishi bilan bu yerda sanasi ko‘rinadi.</span>';
      setCourseCover(coverUrl(state.course));
      return;
    }
    host.innerHTML = groups.map(group => {
      const selected = group.id === state.groupId;
      return `<button type="button" class="stub-option" role="radio" aria-checked="${selected}" tabindex="${selected ? 0 : -1}" data-group="${group.id}" aria-label="${escapeHtml(formatLongDate(group.start_date))}">${stubParts(group.start_date)}</button>`;
    }).join('');
    $$('[data-group]', host).forEach((button, index, buttons) => {
      button.addEventListener('click', () => selectGroup(Number(button.dataset.group)));
      button.addEventListener('keydown', event => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = buttons[(index + step + buttons.length) % buttons.length];
        selectGroup(Number(next.dataset.group));
        $(`[data-group="${next.dataset.group}"]`, host).focus();
      });
    });
    const group = groups.find(item => item.id === state.groupId);
    const days = daysUntil(group.start_date);
    const teachers = group.teachers.join(', ') || 'tez orada e’lon qilinadi';
    $('#groupDetail').innerHTML = `<strong>${escapeHtml(formatLongDate(group.start_date))}</strong> · ${group.number_of_days} kun<span>Ustoz: ${escapeHtml(teachers)} · ${days === 1 ? 'ertaga boshlanadi' : `${days} kundan keyin boshlanadi`}</span>`;
    setCourseCover(group.banner_url || coverUrl(state.course));
  }

  function selectGroup(groupId) {
    if (groupId === state.groupId) return;
    state.groupId = groupId;
    haptic();
    renderGroups();
  }

  const perkHtml = () => `<details class="perk">
      <summary>Imtiyoz bo‘yicha chegirma</summary>
      <p class="hint">Pensioner, nogironligi bor yoki talaba bo‘lsa — hujjat asosida 100 000 so‘mgacha chegirma.</p>
      <label class="field"><span>Toifa</span><select name="eligibility_category">
        <option value="">Imtiyoz yo‘q</option><option value="pensioner">Pensioner</option><option value="disability">Nogironligi bor</option><option value="student">Talaba</option>
      </select></label>
      <label class="field eligibility-file" hidden><span>Tasdiqlovchi hujjat</span><input type="file" name="eligibility_file" accept=".pdf,.jpg,.jpeg,.png"><small>PDF, JPG yoki PNG, 5 MB gacha</small></label>
    </details>`;

  function bindPerk(node) {
    $('[name="eligibility_category"]', node).addEventListener('change', event => {
      $('.eligibility-file', node).hidden = !event.target.value;
      delete node.dataset.proofKey;
      delete node.dataset.proofId;
      updateTotal();
    });
    $('[name="eligibility_file"]', node).addEventListener('change', () => { delete node.dataset.proofKey; });
  }

  function renderSelf() {
    const name = state.profile.full_name || 'Siz';
    const node = $('#selfPerson');
    delete node.dataset.proofKey;
    delete node.dataset.proofId;
    node.innerHTML = `<div class="person__row"><span class="initials">${escapeHtml(initials(name))}</span><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(state.profile.phone_number || '')}</small></div></div>${perkHtml()}`;
    bindPerk(node);
  }

  function setPurchaseType(type) {
    state.type = type;
    $$('[data-purchase-type]').forEach(button => {
      const active = button.dataset.purchaseType === type;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-checked', String(active));
    });
    $('#familyMembers').hidden = type !== 'family';
    $('#addFamilyMember').hidden = type !== 'family';
    if (type === 'family' && !$$('.member').length) addFamilyMember();
    updateTotal();
  }

  function renumberMembers() {
    $$('.member').forEach((node, index) => {
      $('.member__title', node).textContent = `${index + 1}-oila a’zosi`;
      $('.member__remove', node).setAttribute('aria-label', `${index + 1}-oila a’zosini olib tashlash`);
    });
  }

  function addFamilyMember() {
    if ($$('.member').length >= 7) return toast('Bitta xaridga ko‘pi bilan 7 ta oila a’zosi qo‘shiladi.', true);
    const node = document.createElement('article');
    node.className = 'member';
    node.innerHTML = `<p class="member__title"></p><button class="member__remove" type="button">×</button>
      <label class="field"><span>Ism va familiya</span><input name="full_name" autocomplete="off" placeholder="Masalan: Dilnoza Karimova"></label>
      <label class="field"><span>Telefon raqami</span><input name="phone_number" type="tel" inputmode="tel" autocomplete="off" placeholder="+998 90 123 45 67"></label>${perkHtml()}`;
    bindPerk(node);
    $('.member__remove', node).addEventListener('click', () => {
      node.remove();
      renumberMembers();
      if (!$$('.member').length) setPurchaseType('self'); else updateTotal();
    });
    $('#familyMembers').append(node);
    renumberMembers();
    updateTotal();
    return node;
  }

  function priceQuote() {
    const course = state.course;
    const count = 1 + (state.type === 'family' ? $$('.member').length : 0);
    const price = Number(course.price);
    const perPerson = Math.min(price, Math.max(0, ...(course.participant_discounts || [])
      .filter(rule => count >= rule.min_participants).map(rule => Number(rule.amount))));
    const unit = price - perPerson;
    const holders = [$('#selfPerson'), ...(state.type === 'family' ? $$('.member') : [])];
    const eligible = holders.filter(node => $('[name="eligibility_category"]', node)?.value).length;
    const social = Math.min(100000, unit) * eligible;
    const total = unit * count - social;
    const bookingDiscount = Math.min(total, Math.min(unit, Number(course.booking_discount || 0)) * count);
    const minimum = Number(course.minimum_booking || 0) * count;
    return { count, perPerson, social, total, bookingDiscount, minimum, bookingAvailable: total - bookingDiscount >= minimum };
  }

  function updateTotal() {
    if (!state.course) return;
    const quote = priceQuote();
    if (state.payMode === 'booking' && !quote.bookingAvailable) state.payMode = 'full';
    if (!state.payMode) state.payMode = quote.bookingAvailable ? 'booking' : 'full';
    const booking = $('[data-pay-mode="booking"]');
    booking.disabled = !quote.bookingAvailable;
    $('#bookingText').innerHTML = quote.bookingAvailable
      ? `${quote.bookingDiscount ? `<b class="saving">−${money(quote.bookingDiscount)} chegirma.</b> ` : ''}Hozir kamida ${money(quote.minimum)}, qolganini keyinroq to‘laysiz.`
      : 'Bu summa uchun bron qilib bo‘lmaydi.';
    $('#bookingSum').textContent = money(quote.total - quote.bookingDiscount);
    $('#fullSum').textContent = money(quote.total);
    $$('[data-pay-mode]').forEach(button => button.setAttribute('aria-checked', String(button.dataset.payMode === state.payMode)));

    const bookingSaving = state.payMode === 'booking' ? quote.bookingDiscount : 0;
    const saving = quote.perPerson * quote.count + quote.social + bookingSaving;
    $('#buyTotal').textContent = money(quote.total - bookingSaving);
    $('#buyNote').innerHTML = `${quote.count} kishi${saving ? ` · <b class="saving">−${money(saving)}</b>` : ''}`;
  }

  async function uploadProof(node, phone) {
    const category = $('[name="eligibility_category"]', node).value;
    if (!category) return null;
    const file = $('[name="eligibility_file"]', node).files[0];
    if (!file || file.size > 5 * 1024 * 1024) {
      $('.perk', node).open = true;
      throw new Error('Imtiyoz uchun 5 MB gacha bo‘lgan tasdiqlovchi hujjatni yuklang.');
    }
    const key = `${category}:${phone}:${file.name}:${file.size}:${file.lastModified}`;
    if (node.dataset.proofKey === key) return Number(node.dataset.proofId);
    const body = new FormData();
    body.append('category', category);
    body.append('phone_number', phone);
    body.append('document', file);
    const result = await api('/telegram-app/api/eligibility-documents/', { method: 'POST', body });
    node.dataset.proofKey = key;
    node.dataset.proofId = result.id;
    return result.id;
  }

  async function buy() {
    const course = state.course;
    const group = openGroups(course).find(item => item.id === state.groupId);
    if (!group) return toast('Boshlanish sanasini tanlang.', true);
    const memberNodes = state.type === 'family' ? $$('.member') : [];
    const members = memberNodes.map(node => ({
      full_name: $('[name="full_name"]', node).value.trim(),
      phone_number: $('[name="phone_number"]', node).value.trim(),
    }));
    const emptyInput = memberNodes.flatMap(node => $$('input[name="full_name"], input[name="phone_number"]', node)).find(input => !input.value.trim());
    if (emptyInput) {
      emptyInput.focus();
      return toast('Har bir oila a’zosining ismi va telefon raqamini kiriting.', true);
    }
    const button = $('#buyButton');
    button.disabled = true;
    button.textContent = 'Tayyorlanmoqda…';
    try {
      const eligibilityDocument = await uploadProof($('#selfPerson'), state.profile.phone_number);
      for (let index = 0; index < members.length; index++) {
        members[index].eligibility_document_id = await uploadProof(memberNodes[index], members[index].phone_number);
      }
      const data = await api('/telegram-app/api/purchases/', {
        method: 'POST',
        body: JSON.stringify({
          course_id: course.id, group_id: group.id, purchase_type: state.type, members,
          eligibility_document_id: eligibilityDocument, payment_mode: state.payMode,
        }),
      });
      // The server closes an earlier unpaid order for the same course.
      state.purchases = state.purchases.filter(item => !(item.course_id === course.id && isUnpaid(item)));
      replacePurchase(data.purchase);
      await openCheckout(data.purchase, true);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = 'Sotib olish';
    }
  }

  // ---- Checkout: read and accept the contract, then pay

  const isFinished = purchase => ['success', 'refunded'].includes(purchase.payment_status);

  async function openCheckout(purchase, fromCourse = false) {
    state.purchase = purchase;
    state.checkoutFromCourse = fromCourse;
    $('#checkoutBackLabel').textContent = fromCourse ? 'Kursga qaytish' : 'Profil';
    const needsContract = !purchase.contract_accepted && !isFinished(purchase);
    if (needsContract) {
      try {
        const data = await api(`/telegram-app/api/purchases/${purchase.id}/contract/`);
        mountContract(data.document);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    $('#contractBlock').hidden = !needsContract;
    $('#contractDone').hidden = needsContract;
    // The purchase steps only describe the first payment.
    $('#checkoutView .steps').hidden = Number(purchase.paid_amount) > 0;
    renderOrder(purchase);
    renderPayControls();
    showView('checkoutView', fromCourse ? () => showView('courseView', showCatalog) : showProfile);
  }

  function renderOrder(purchase) {
    const group = purchase.group;
    const course = state.courses.find(item => item.id === purchase.course_id);
    const cover = group?.banner_url || (course ? coverUrl(course) : '');
    const row = (label, value, tone = '') => `<div class="order__row ${tone}"><span>${label}</span><strong>${value}</strong></div>`;
    const gross = Number(purchase.total_amount) + Number(purchase.discount_total || 0) + Number(purchase.social_discount_amount || 0);
    const names = purchase.members.map(member => member.full_name).join(', ');
    const paid = Number(purchase.paid_amount);
    $('#orderSummary').innerHTML = `
      <div class="order__head"><span class="order__thumb">${coverHtml(purchase.course_id, cover)}</span><div>
        <strong>${escapeHtml(purchase.course)}</strong>
        <small>${group ? `${escapeHtml(formatLongDate(group.start_date))} · ${group.number_of_days} kun` : 'Guruh markaz tomonidan biriktiriladi'}</small>
        <small>${escapeHtml(names)}</small>
      </div></div>`
      + row(`Kurs narxi${purchase.participant_count > 1 ? ` · ${purchase.participant_count} kishi` : ''}`, money(gross))
      + (Number(purchase.discount_total) > 0 ? row(escapeHtml(purchase.discount_name || 'Oila chegirmasi'), `−${money(purchase.discount_total)}`, 'order__row--saving') : '')
      + (Number(purchase.social_discount_amount) > 0 ? row('Imtiyoz chegirmasi', `−${money(purchase.social_discount_amount)}`, 'order__row--saving') : '')
      + (purchase.is_booking && Number(purchase.booking_discount) > 0 ? row(paid ? 'Bron chegirmasi' : 'Bron chegirmasi (birinchi to‘lovdan keyin)', `−${money(purchase.booking_discount)}`, 'order__row--saving') : '')
      + row('Jami', money(purchase.sale_amount), 'order__row--total')
      + (paid ? row('To‘langan', money(purchase.paid_amount)) + row('Qolgan', money(purchase.payable_amount)) : '');
    bindCovers($('#orderSummary'));
  }

  function mountContract(documentData) {
    const scroll = $('#checkoutContractScroll');
    const progress = $('#checkoutContractProgress');
    const consent = $('#checkoutConsent');
    const hint = $('#contractHint');
    state.contract = { purchaseId: state.purchase.id, version: documentData.version, read: false };
    scroll.innerHTML = documentData.html;
    consent.checked = false;
    consent.disabled = true;
    hint.textContent = 'Oxirigacha o‘qing';
    hint.classList.remove('is-read');
    progress.style.width = '0%';
    const update = () => {
      if (!scroll.clientHeight) return;
      const maximum = Math.max(scroll.scrollHeight - scroll.clientHeight, 0);
      const ratio = maximum ? Math.min(scroll.scrollTop / maximum, 1) : 1;
      progress.style.width = `${Math.round(ratio * 100)}%`;
      if (ratio >= 0.995 && !state.contract.read) {
        state.contract.read = true;
        consent.disabled = false;
        hint.textContent = 'O‘qildi';
        hint.classList.add('is-read');
        renderPayControls();
      }
    };
    scroll.onscroll = update;
    consent.onchange = renderPayControls;
    requestAnimationFrame(() => { scroll.scrollTop = 0; update(); });
  }

  function paymentAmount(purchase) {
    if (!purchase.is_booking) return Number(purchase.payable_amount);
    return Number($('#installmentAmount').value || purchase.minimum_payment);
  }

  function renderPayControls() {
    const purchase = state.purchase;
    if (!purchase) return;
    const finished = isFinished(purchase);
    const openInvoice = ['creating', 'uncertain', 'ready', 'error'].includes(purchase.invoice_state);
    const busy = ['creating', 'uncertain'].includes(purchase.invoice_state);
    const contract = state.contract?.purchaseId === purchase.id ? state.contract : null;
    const consented = Boolean(contract?.read && $('#checkoutConsent').checked);

    $('#contractVersionLabel').textContent = purchase.contract_version ? `Versiya ${purchase.contract_version}` : '';
    const field = $('#installmentField');
    field.hidden = !purchase.is_booking || finished;
    const input = $('#installmentAmount');
    const inputKey = `${purchase.id}:${purchase.paid_amount}:${openInvoice ? purchase.invoice_amount : 'new'}`;
    if (input.dataset.paymentKey !== inputKey) {
      input.value = openInvoice ? purchase.invoice_amount : purchase.minimum_payment;
      input.dataset.paymentKey = inputKey;
    }
    input.min = purchase.minimum_payment;
    input.max = purchase.payable_amount;
    input.disabled = openInvoice;
    $('#payRemaining').hidden = openInvoice;
    $('#payRemaining').textContent = Number(purchase.paid_amount) ? 'Qolgan summani to‘liq to‘lash' : 'Butun summani to‘lash';
    $('#installmentHint').textContent = openInvoice
      ? `Ochilgan to‘lov: ${money(purchase.invoice_amount)}. Avval shu to‘lovni yakunlang.`
      : `Kamida ${money(purchase.minimum_payment)}, ko‘pi bilan ${money(purchase.payable_amount)}.`;

    const button = $('#payButton');
    button.hidden = finished;
    const due = money(paymentAmount(purchase));
    if (!purchase.contract_accepted) {
      button.disabled = busy || !consented;
      button.textContent = !contract?.read ? 'Avval shartnomani o‘qing' : !consented ? 'Shartnomaga rozilik bildiring' : 'Shartnomani qabul qilaman va to‘layman';
    } else {
      button.disabled = busy;
      button.textContent = purchase.checkout_url ? 'To‘lov sahifasini ochish' : `${due} to‘lash`;
    }

    let status = '';
    if (busy) status = 'To‘lov holati aniqlanmoqda. Qayta to‘lamang — bir necha daqiqadan so‘ng tekshiring.';
    else if (purchase.checkout_url) status = 'To‘lov sahifasi ochilgan. To‘lovni yakunlagach, shu yerga qayting.';
    else if (purchase.payment_status === 'failed') status = 'Oldingi to‘lov o‘tmadi. Qayta urinib ko‘ring.';
    $('#paymentStatus').textContent = status;
    $('#paymentNote').textContent = demo ? 'Demo rejim: pul yechilmaydi.' : 'To‘lov Multicard sahifasida ochiladi. Yakunlagach, ilovaga qayting.';
    $('#paymentNote').hidden = finished;
    $('#checkPayment').hidden = demo || !purchase.invoice_state || finished;
    $('#cancelPurchase').hidden = !purchase.can_cancel;
  }

  async function pay() {
    const purchase = state.purchase;
    if (purchase.is_booking && !$('#installmentAmount').reportValidity()) return;
    const button = $('#payButton');
    if (!purchase.contract_accepted) {
      button.disabled = true;
      button.textContent = 'Shartnoma saqlanmoqda…';
      try {
        const data = await api(`/telegram-app/api/purchases/${purchase.id}/contract/accept/`, {
          method: 'POST',
          body: JSON.stringify({ accepted: true, version: state.contract.version }),
        });
        state.purchase = data.purchase;
        replacePurchase(data.purchase);
        $('#contractBlock').hidden = true;
        $('#contractDone').hidden = false;
      } catch (error) {
        toast(error.message, true);
        renderPayControls();
        return;
      }
    }
    await startPayment();
  }

  async function startPayment() {
    const purchaseId = state.purchase.id;
    const payload = state.purchase.is_booking ? { amount: $('#installmentAmount').value, expected_paid: state.purchase.paid_amount } : {};
    try { localStorage.setItem('nmPaymentAttempt', JSON.stringify({ id: purchaseId, paid: state.purchase.paid_amount })); } catch (_) {}
    const button = $('#payButton');
    button.disabled = true;
    button.textContent = 'To‘lov tayyorlanmoqda…';
    try {
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/${demo ? 'demo-payment' : 'payment'}/`, { method: 'POST', body: JSON.stringify(payload) });
      applyPayment(data.purchase);
      if (data.purchase.payment_status !== 'success' && data.checkout_url) {
        const url = new URL(data.checkout_url);
        if (url.protocol !== 'https:') throw new Error('To‘lov havolasi noto‘g‘ri. Administrator bilan bog‘laning.');
        if (tg?.initData && tg.openLink) tg.openLink(url.href);
        else window.location.assign(url.href);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (state.purchase?.id === purchaseId && $('#checkoutView').classList.contains('is-active')) renderPayControls();
    }
  }

  function applyPayment(purchase) {
    replacePurchase(purchase);
    if (state.purchase?.id !== purchase.id) return;
    const previousPaid = Number(state.purchase.paid_amount || 0);
    state.purchase = purchase;
    if (['partial', 'success'].includes(purchase.payment_status) && Number(purchase.paid_amount) > previousPaid) {
      showPaymentResult(purchase, previousPaid);
      return;
    }
    if ($('#checkoutView').classList.contains('is-active')) {
      renderOrder(purchase);
      renderPayControls();
    }
  }

  let checkingPayment = false;
  async function checkPayment(remote = false) {
    const purchase = state.purchase;
    if (checkingPayment || !purchase || !purchase.invoice_state || !$('#checkoutView').classList.contains('is-active')) return;
    checkingPayment = true;
    $('#checkPayment').disabled = true;
    try {
      const data = await api(`/telegram-app/api/purchases/${purchase.id}/payment/${remote ? 'check' : 'status'}/`, remote ? { method: 'POST', body: '{}' } : {});
      applyPayment(data.purchase);
      if (remote && data.purchase.payment_status !== 'success') toast(data.purchase.payment_status_label);
    } catch (error) {
      if (remote) toast(error.message, true);
    } finally {
      checkingPayment = false;
      $('#checkPayment').disabled = false;
    }
  }

  async function cancelPurchase(id) {
    const purchase = state.purchases.find(item => item.id === id);
    if (!purchase || !(await confirmAction(`«${purchase.course}» buyurtmasi bekor qilinsinmi?`))) return;
    try {
      await api(`/telegram-app/api/purchases/${id}/cancel/`, { method: 'POST', body: '{}' });
      state.purchases = state.purchases.filter(item => item.id !== id);
      renderTodoIndicators();
      toast('Buyurtma bekor qilindi.');
      if ($('#checkoutView').classList.contains('is-active')) showProfile(); else renderTodos();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function replacePurchase(purchase) {
    const index = state.purchases.findIndex(item => item.id === purchase.id);
    if (index >= 0) state.purchases[index] = purchase; else state.purchases.unshift(purchase);
    renderTodoIndicators();
  }

  // ---- After payment

  // A payment can place the buyer in a group, so courses and orders are reloaded.
  async function refreshAccount() {
    try {
      const data = await api('/telegram-app/api/bootstrap/');
      Object.assign(state, { courses: data.courses, myCourses: data.my_courses || [], purchases: data.purchases });
      renderTodoIndicators();
      if ($('#profileView').classList.contains('is-active')) { renderTodos(); renderMyCourses(); }
    } catch (_) {}
  }

  function showPaymentResult(purchase, previousPaid = 0) {
    try { localStorage.removeItem('nmPaymentAttempt'); } catch (_) {}
    state.purchase = purchase;
    $('#paymentResultAmount').textContent = money(Number(purchase.paid_amount) - previousPaid);
    $('#paymentResultPaid').textContent = money(purchase.paid_amount);
    $('#paymentResultDebt').textContent = money(purchase.remaining_amount);
    $('#paymentResultNote').textContent = Number(purchase.remaining_amount) > 0
      ? 'Bron qabul qilindi. Qolgan summani «Profil» bo‘limidan istalgan vaqtda to‘lashingiz mumkin.'
      : 'Kurs uchun to‘lov to‘liq yakunlandi.';
    $('#continueAfterPayment').hidden = purchase.questionnaire_completed;
    showView('paymentResultView');
    refreshAccount();
  }

  function openQuestionnaire(purchase) {
    state.purchase = purchase;
    renderQuestionnaires();
    showView('questionnaireView', showProfile);
  }

  function renderQuestionnaires() {
    $('#questionnaireMembers').innerHTML = state.purchase.members.map((member, index) => `
      <article class="questionnaire-card" data-member-id="${member.id}" data-phone="${escapeHtml(member.phone_number)}">
        <h3>${escapeHtml(member.full_name)}</h3><p>${index === 0 ? 'Xaridor' : 'Oila a’zosi'} · ${escapeHtml(member.phone_number)}</p>
        <label class="field"><span>Tug‘ilgan sana *</span><input type="date" name="birth_date" required></label>
        <label class="field"><span>Shahar yoki tuman *</span><input name="city" placeholder="Masalan: Toshkent, Chilonzor" required></label>
        <label class="field"><span>Kasb yoki faoliyat</span><input name="occupation"></label>
        <label class="field"><span>Kursdan nimani kutasiz? *</span><textarea name="learning_goal" required></textarea></label>
        <label class="field"><span>Shunga o‘xshash kurslarda qatnashganmisiz?</span><textarea name="prior_experience"></textarea></label>
        <label class="field"><span>Sog‘liq bo‘yicha bilishimiz kerak bo‘lgan ma’lumot</span><textarea name="health_notes"></textarea></label>
        <label class="consent"><input type="checkbox" name="consent" required><span>Ma’lumotlar to‘g‘ri. Ulardan kursni tashkil etish uchun foydalanishga roziman.</span></label>
      </article>`).join('');
    $$('.questionnaire-card').forEach(card => {
      const answer = state.answers[card.dataset.phone];
      if (!answer) return;
      ['birth_date', 'city', 'occupation', 'learning_goal', 'prior_experience', 'health_notes'].forEach(name => { $(`[name="${name}"]`, card).value = answer[name] || ''; });
    });
  }

  async function submitQuestionnaire(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    const responses = $$('.questionnaire-card').map(card => ({
      member_id: Number(card.dataset.memberId),
      birth_date: $('[name="birth_date"]', card).value,
      city: $('[name="city"]', card).value.trim(),
      occupation: $('[name="occupation"]', card).value.trim(),
      learning_goal: $('[name="learning_goal"]', card).value.trim(),
      prior_experience: $('[name="prior_experience"]', card).value.trim(),
      health_notes: $('[name="health_notes"]', card).value.trim(),
      consent: $('[name="consent"]', card).checked,
    }));
    $$('.questionnaire-card').forEach((card, index) => { state.answers[card.dataset.phone] = responses[index]; });
    try {
      const data = await api(`/telegram-app/api/purchases/${state.purchase.id}/questionnaire/`, { method: 'POST', body: JSON.stringify({ responses }) });
      state.purchase = data.purchase;
      replacePurchase(data.purchase);
      showSuccess(data.purchase);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function showSuccess(purchase) {
    $('#successDescription').textContent = Number(purchase.payable_amount) > 0
      ? `Anketa qabul qilindi. Qolgan to‘lov: ${money(purchase.payable_amount)} — uni «Profil» bo‘limidan to‘lashingiz mumkin.`
      : 'To‘lov va anketa qabul qilindi. Kurs haqidagi xabarlar shu Telegram bot orqali keladi.';
    showView('successView');
    refreshAccount();
  }

  // ---- My courses and attendance

  function courseStats(course) {
    const lessons = course.participants.flatMap(participant => participant.lessons || []);
    const attended = lessons.filter(lesson => ['attended', 'late'].includes(lesson.status)).length;
    const marked = lessons.filter(lesson => lesson.status !== 'unmarked').length;
    return { attended, marked, total: lessons.length };
  }

  const courseState = course => (course.assignment_status === 'awaiting_group'
    ? ['Guruh biriktirilmoqda', 'waiting'] : course.is_active ? ['Faol guruh', 'active'] : ['Yakunlangan', 'complete']);

  function renderMyCourses() {
    const host = $('#myCourseList');
    if (!state.myCourses.length) {
      host.innerHTML = todoPurchases().length ? '' : '<div class="empty"><h3>Hali kurs sotib olinmagan</h3><p>Kursni tanlang — to‘lovdan keyin u shu yerda ko‘rinadi.</p><button type="button" class="btn btn--primary" data-open-catalog>Kurslarni ko‘rish</button></div>';
      $('[data-open-catalog]', host)?.addEventListener('click', showCatalog);
      return;
    }
    const label = todoPurchases().length ? '<p class="section-label">To‘langan kurslar</p>' : '';
    host.innerHTML = `${label}${state.myCourses.map(course => {
      const stats = courseStats(course);
      const [label, tone] = courseState(course);
      const awaiting = course.assignment_status === 'awaiting_group';
      const progress = stats.total ? Math.round(stats.attended * 100 / stats.total) : 0;
      return `<button class="my-course" type="button" data-my-course="${escapeHtml(course.id)}">
        <span class="my-course__head"><span class="state-tag state-tag--${tone}">${label}</span><span>${course.participants.length} kishi</span></span>
        <strong>${escapeHtml(course.course)}</strong>
        <small>${awaiting ? 'Markaz guruhni biriktirgach, darslar shu yerda ko‘rinadi' : `${formatDate(course.start_date)} · ${course.number_of_days} kun`}</small>
        <span class="progress" aria-hidden="true"><i style="width:${progress}%"></i></span>
        <span class="my-course__foot"><span>${awaiting ? 'Davomat hali boshlanmagan' : `${stats.attended} ta darsda qatnashdi`}</span><b>Batafsil</b></span>
      </button>`;
    }).join('')}`;
    $$('[data-my-course]', host).forEach(button => button.addEventListener('click', () => openCourseDetail(button.dataset.myCourse)));
  }

  function openCourseDetail(courseId) {
    const course = state.myCourses.find(item => String(item.id) === String(courseId));
    if (!course) return;
    const banner = $('#courseDetailBanner');
    banner.hidden = !course.banner_url;
    banner.onerror = () => { banner.hidden = true; };
    if (course.banner_url) banner.src = course.banner_url; else banner.removeAttribute('src');
    const [label, tone] = courseState(course);
    const awaiting = course.assignment_status === 'awaiting_group';
    $('#courseDetailTitle').textContent = course.course;
    $('#courseDetailState').textContent = label;
    $('#courseDetailState').className = `state-tag state-tag--${tone}`;
    $('#courseDetailMeta').innerHTML = awaiting
      ? `<div><small>Holat</small><strong>Guruh biriktirilmoqda</strong></div><div><small>Davomiyligi</small><strong>${course.number_of_days} kun</strong></div>`
      : `<div><small>Boshlanish</small><strong>${formatDate(course.start_date)}</strong></div><div><small>Davomiyligi</small><strong>${course.number_of_days} kun</strong></div><div class="facts__wide"><small>Ustoz</small><strong>${escapeHtml(course.teachers.join(', ') || 'Tez orada e’lon qilinadi')}</strong></div>`;
    const host = $('#courseDetailParticipants');
    if (awaiting) {
      host.innerHTML = `<div class="awaiting"><h3>Davomat guruh bilan ochiladi</h3><p>Markaz guruhni biriktirgach, har bir dars sanasi va davomatingiz shu yerda ko‘rinadi.</p><p>${course.participants.map(item => escapeHtml(item.full_name)).join(', ')}</p></div>`;
    } else {
      host.innerHTML = course.participants.map(participant => {
        const stats = courseStats({ participants: [participant] });
        return `<section class="participant">
          <div class="participant__head"><span class="initials">${escapeHtml(initials(participant.full_name))}</span><div><h3>${escapeHtml(participant.full_name)}</h3><p>${escapeHtml(participant.status_label)}</p></div><strong>${stats.attended}/${participant.lessons.length}</strong></div>
          <ol class="lessons">${participant.lessons.map(lesson => `
            <li class="lesson lesson--${['attended', 'late', 'absent', 'excused'].includes(lesson.status) ? lesson.status : 'unmarked'}">
              <span class="lesson__dot" aria-hidden="true"></span>
              <span>${lesson.day_number}-dars · ${formatDate(lesson.date)}${lesson.marked_at ? `<small>${formatDateTime(lesson.marked_at)} · ${escapeHtml(lesson.marked_by || 'Markaz xodimi')}${lesson.reason ? ` · Sabab: ${escapeHtml(lesson.reason)}` : ''}</small>` : ''}</span>
              <b>${escapeHtml(lesson.status_label)}</b>
            </li>`).join('')}</ol>
        </section>`;
      }).join('');
    }
    showView('courseDetailView', showProfile);
  }

  // ---- Profile and legal documents

  function renderProfile() {
    const name = state.profile.full_name || 'Foydalanuvchi';
    $('#greeting').textContent = `Assalomu alaykum, ${name.split(' ')[0]}`;
    $('#profileName').textContent = name;
    $('#profilePhone').textContent = state.profile.phone_number || '—';
    $('#profileAvatar').textContent = initials(name);
    $('#profileButton').textContent = initials(name);
  }

  function mountTerms(documentData, readOnly) {
    const scroll = $('#termsScroll');
    const progress = $('#termsProgress');
    const consent = $('#termsConsent');
    const button = $('#acceptTerms');
    state.legal = { version: documentData.version, readOnly };
    scroll.innerHTML = documentData.html;
    consent.checked = false;
    consent.closest('.legal-consent').hidden = readOnly;
    $('#termsIntro').textContent = readOnly
      ? 'Siz bu versiyani qabul qilgansiz. To‘liq matnni istalgan vaqtda qayta o‘qishingiz mumkin.'
      : 'Davom etishdan oldin hujjatni oxirigacha o‘qing. Roziligingiz sana, qurilma va hujjat versiyasi bilan saqlanadi.';
    consent.disabled = !readOnly;
    button.disabled = !readOnly;
    button.textContent = readOnly ? 'Yopish' : 'Oxirigacha o‘qing';
    const update = () => {
      if (!scroll.clientHeight) return;
      const maximum = Math.max(scroll.scrollHeight - scroll.clientHeight, 0);
      const ratio = maximum ? Math.min(scroll.scrollTop / maximum, 1) : 1;
      progress.style.width = `${Math.round(ratio * 100)}%`;
      if (!readOnly && ratio >= 0.995) {
        consent.disabled = false;
        button.textContent = 'Qabul qilaman';
        button.disabled = !consent.checked;
      }
    };
    scroll.onscroll = update;
    consent.onchange = update;
    requestAnimationFrame(() => { scroll.scrollTop = 0; update(); });
  }

  async function openTerms(required = false) {
    try {
      const data = await api('/telegram-app/api/legal/terms/');
      mountTerms(data.document, !required && data.document.accepted);
      showView('termsView', required ? null : showProfile);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function acceptTerms() {
    if (state.legal.readOnly) return showProfile();
    const button = $('#acceptTerms');
    button.disabled = true;
    button.textContent = 'Saqlanmoqda…';
    try {
      await api('/telegram-app/api/legal/terms/accept/', { method: 'POST', body: JSON.stringify({ accepted: true, version: state.legal.version }) });
      toast('Foydalanish shartlari qabul qilindi.');
      showCatalog();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = 'Qabul qilaman';
    }
  }

  async function openContractCopy() {
    const purchase = state.purchase;
    try {
      const data = await api(`/telegram-app/api/purchases/${purchase.id}/contract/`);
      $('#contractScroll').innerHTML = data.document.html;
      showView('contractView', () => openCheckout(purchase, state.checkoutFromCourse));
    } catch (error) {
      toast(error.message, true);
    }
  }

  // ---- Start

  function resumeLastPayment() {
    let attempt;
    try { attempt = JSON.parse(localStorage.getItem('nmPaymentAttempt')); } catch (_) {}
    const purchase = attempt && state.purchases.find(item => item.id === attempt.id);
    if (!purchase) return;
    if (['partial', 'success'].includes(purchase.payment_status) && Number(purchase.paid_amount) > Number(attempt.paid)) showPaymentResult(purchase, Number(attempt.paid));
    else if (!isFinished(purchase)) openCheckout(purchase);
  }

  async function bootstrap() {
    try {
      const data = await api('/telegram-app/api/bootstrap/');
      Object.assign(state, { profile: data.profile, courses: data.courses, myCourses: data.my_courses || [], purchases: data.purchases });
      renderProfile();
      renderCatalog();
      renderTodoIndicators();
      if (data.legal.terms_required) openTerms(true);
      else resumeLastPayment();
    } catch (error) {
      $('#catalog').innerHTML = `<li class="empty"><h3>Ilovani ochib bo‘lmadi</h3><p>${escapeHtml(error.message)} Botdagi «Kurslar» tugmasi orqali qayta oching.</p></li>`;
      toast(error.message, true);
    }
  }

  if (tg?.BackButton && supports('6.1') && tg.initData) {
    document.body.classList.add('has-telegram-back');
    tg.BackButton.onClick(goBack);
  }
  $$('[data-back]').forEach(button => button.addEventListener('click', goBack));
  $$('[data-tab]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.tab === 'profile') showProfile();
    else showCatalog();
  }));
  $$('[data-show-profile]').forEach(button => button.addEventListener('click', showProfile));
  $$('[data-purchase-type]').forEach(button => button.addEventListener('click', () => setPurchaseType(button.dataset.purchaseType)));
  $$('[data-pay-mode]').forEach(button => button.addEventListener('click', () => {
    state.payMode = button.dataset.payMode;
    haptic();
    updateTotal();
  }));
  $('#profileButton').addEventListener('click', showProfile);
  $('#todoBanner').addEventListener('click', showProfile);
  $('#addFamilyMember').addEventListener('click', () => {
    const node = addFamilyMember();
    if (node) $('[name="full_name"]', node).focus();
  });
  $('#buyButton').addEventListener('click', buy);
  $('#payButton').addEventListener('click', pay);
  $('#installmentAmount').addEventListener('input', renderPayControls);
  $('#payRemaining').addEventListener('click', () => { $('#installmentAmount').value = state.purchase.payable_amount; renderPayControls(); });
  $('#cancelPurchase').addEventListener('click', () => cancelPurchase(state.purchase.id));
  $('#contractDone').addEventListener('click', openContractCopy);
  $('#continueAfterPayment').addEventListener('click', () => openQuestionnaire(state.purchase));
  $('#questionnaireForm').addEventListener('submit', submitQuestionnaire);
  $('#acceptTerms').addEventListener('click', acceptTerms);
  $('#viewTerms').addEventListener('click', () => openTerms(false));
  $('#checkPayment').addEventListener('click', () => checkPayment(true));
  window.addEventListener('focus', () => checkPayment());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkPayment(); });
  setInterval(() => { if (!document.hidden) checkPayment(); }, 5000);
  bootstrap();
})();
