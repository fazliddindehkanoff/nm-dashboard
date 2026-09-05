(() => {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (!tg.isVersionAtLeast || tg.isVersionAtLeast('6.1')) {
      tg.setHeaderColor('#00213D');
      tg.setBackgroundColor('#F7FAFC');
    }
  }

  const state = {
    profile: null,
    courses: [],
    purchases: [],
    course: null,
    type: 'self',
    purchase: null,
    legal: null,
    legalReadOnly: false,
    legalReturnView: 'homeView',
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const money = value => `${new Intl.NumberFormat('uz-UZ', { maximumFractionDigits: 2 }).format(Number(value || 0))} UZS`;
  const initials = name => (name || 'N').split(/\s+/).slice(0, 2).map(v => v[0]).join('').toUpperCase();
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const csrf = $('meta[name="csrf-token"]').content;

  function headers(json = false) {
    const result = { 'X-Telegram-Init-Data': tg?.initData || '' };
    if (document.body.dataset.demo === '1') result['X-Telegram-Demo'] = '1';
    if (json) Object.assign(result, { 'Content-Type': 'application/json', 'X-CSRFToken': csrf });
    return result;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({ ok: false, error: 'Server javobi noto‘g‘ri.' }));
    if (!response.ok || !data.ok) throw new Error(data.error || 'Xatolik yuz berdi.');
    return data;
  }

  let toastTimer;
  function toast(message, error = false) {
    const node = $('#toast'); node.textContent = message; node.classList.toggle('is-error', error); node.classList.add('is-visible');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('is-visible'), 3200);
  }

  function setJourney(step) {
    $$('.journey__step').forEach((node, index) => node.classList.toggle('is-active', index < step));
    $$('.journey__line').forEach((node, index) => { node.style.background = index < step - 1 ? '#3BC9D4' : ''; });
  }

  function showView(id, step = 1) {
    $$('.view').forEach(node => node.classList.toggle('is-active', node.id === id));
    $('.hero').style.display = ['homeView', 'profileView'].includes(id) ? '' : 'none';
    $('.bottom-nav').style.display = ['homeView', 'profileView'].includes(id) ? '' : 'none';
    setJourney(step); window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function configureLegalGate(kind, documentData, readOnly = false) {
    const prefix = kind === 'terms' ? 'terms' : 'contract';
    const scroll = $(`#${prefix}Scroll`);
    const progress = $(`#${prefix}Progress`);
    const consent = $(`#${prefix}Consent`);
    const button = $(`#accept${kind === 'terms' ? 'Terms' : 'Contract'}`);
    const intro = $(`#${prefix}Intro`);
    const label = consent.closest('.legal-consent');
    scroll.innerHTML = documentData.html;
    scroll.scrollTop = 0;
    consent.checked = false;
    state.legal = { kind, version: documentData.version };
    state.legalReadOnly = readOnly;

    if (readOnly) {
      label.hidden = true;
      button.disabled = false;
      button.textContent = 'Yopish';
      intro.textContent = kind === 'terms'
        ? 'Siz ushbu versiyani avval qabul qilgansiz. To‘liq matnni istalgan vaqtda qayta ko‘rishingiz mumkin.'
        : 'Ushbu shartnoma elektron ravishda qabul qilingan. Xaridga biriktirilgan to‘liq matn quyida saqlanadi.';
    } else {
      label.hidden = false;
      consent.disabled = true;
      button.disabled = true;
      button.textContent = 'Oxirigacha o‘qing';
      intro.textContent = kind === 'terms'
        ? 'Davom etishdan oldin hujjatni oxirigacha o‘qing. Roziligingiz sana, qurilma va hujjat versiyasi bilan saqlanadi.'
        : 'Shartnoma aynan shu xarid ma’lumotlari asosida tayyorlandi. Oxirigacha o‘qigach tasdiqlash faollashadi.';
    }

    const updateGate = () => {
      const maximum = Math.max(scroll.scrollHeight - scroll.clientHeight, 0);
      const ratio = maximum ? Math.min(scroll.scrollTop / maximum, 1) : 1;
      progress.style.width = `${Math.round(ratio * 100)}%`;
      if (!readOnly && ratio >= .995) {
        consent.disabled = false;
        button.textContent = kind === 'terms' ? 'Qabul qilaman' : 'Shartnomani qabul qilaman';
        button.disabled = !consent.checked;
      }
    };
    scroll.onscroll = updateGate;
    consent.onchange = updateGate;
    requestAnimationFrame(() => {
      scroll.scrollTop = 0;
      updateGate();
    });
  }

  async function openTerms(required = false) {
    try {
      const data = await api('/telegram-app/api/legal/terms/');
      state.legalReturnView = required ? 'homeView' : 'profileView';
      configureLegalGate('terms', data.document, !required && data.document.accepted);
      showView('termsView', 1);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function openContract(purchase, readOnly = false) {
    try {
      state.purchase = purchase;
      const data = await api(`/telegram-app/api/purchases/${purchase.id}/contract/`);
      const current = { ...purchase, contract_accepted: data.document.accepted };
      state.purchase = current;
      replacePurchase(current);
      state.legalReturnView = readOnly ? 'homeView' : 'homeView';
      configureLegalGate('contract', data.document, readOnly && data.document.accepted);
      showView('contractView', 2);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderProfile() {
    const name = state.profile.full_name || 'Foydalanuvchi';
    $('#welcomeTitle').textContent = `Salom, ${name.split(' ')[0]}`;
    $('#profileName').textContent = name; $('#profilePhone').textContent = state.profile.phone_number || '—';
    $('#profileAvatar').textContent = initials(name); $('#profileButton').textContent = initials(name);
    $('#selfParticipant').innerHTML = `<span class="person-dot">${escapeHtml(initials(name))}</span><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(state.profile.phone_number)}</small></div>`;
  }

  function renderCourses() {
    $('#courseCount').textContent = `${state.courses.length} ta`;
    $('#courseList').innerHTML = state.courses.length ? state.courses.map(course => `
      <article class="course-card">
        <div class="course-card__top"><span class="course-mark"><img src="/static/main/brand/norbekov-mark.svg" alt=""></span>
          <div><h3>${escapeHtml(course.name)}</h3><p>${course.number_of_days || 0} kunlik rivojlanish dasturi</p></div>
        </div>
        <div class="course-card__bottom"><div class="price"><small>Bir kishi uchun</small><strong>${money(course.price)}</strong></div>
          <button class="select-button" type="button" data-course-id="${course.id}">Tanlash</button></div>
      </article>`).join('') : '<article class="course-card"><h3>Hozircha kurslar yo‘q</h3><p>Yangi kurslar tez orada qo‘shiladi.</p></article>';
    $$('[data-course-id]').forEach(button => button.addEventListener('click', () => startCheckout(Number(button.dataset.courseId))));
  }

  function renderHistory() {
    const host = $('#purchaseHistory');
    if (!state.purchases.length) { host.innerHTML = ''; return; }
    host.innerHTML = `<h2 class="history-heading">Mening kurslarim</h2>${state.purchases.map(item => {
      const done = item.questionnaire_completed && item.payment_status === 'success';
      const paid = item.payment_status === 'success';
      const label = done ? 'Tayyor' : paid ? 'Anketa kutilmoqda' : item.payment_status_label;
      const contractLink = item.contract_accepted ? `<button type="button" class="continue-button" data-view-contract="${item.id}">Shartnomani ko‘rish</button>` : '';
      return `<article class="purchase-card"><div class="purchase-card__head"><div><h3>${escapeHtml(item.course)}</h3><p>${item.participant_count} ishtirokchi · ${money(item.total_amount)}</p></div><span class="status status--${done ? 'success' : 'pending'}">${label}</span></div>${done ? contractLink : `<button type="button" class="continue-button" data-resume="${item.id}">Davom ettirish →</button>${contractLink}`}</article>`;
    }).join('')}`;
    $$('[data-resume]').forEach(button => button.addEventListener('click', () => resumePurchase(Number(button.dataset.resume))));
    $$('[data-view-contract]').forEach(button => button.addEventListener('click', () => {
      const purchase = state.purchases.find(item => item.id === Number(button.dataset.viewContract));
      if (purchase) openContract(purchase, true);
    }));
  }

  function startCheckout(courseId) {
    state.course = state.courses.find(course => course.id === courseId); state.purchase = null; state.type = 'self';
    $('#checkoutCourse').textContent = state.course.name; $$('.segment__item').forEach((node, i) => node.classList.toggle('is-active', i === 0));
    $('#familyArea').hidden = true; $('#familyMembers').innerHTML = ''; updateTotal(); showView('checkoutView', 1);
  }

  function updateTotal() {
    const participants = 1 + (state.type === 'family' ? $$('.family-member').length : 0);
    $('#liveTotal').textContent = money(Number(state.course?.price || 0) * participants);
    $('#participantLabel').textContent = `${participants} ishtirokchi`;
  }

  function addFamilyMember() {
    if ($$('.family-member').length >= 7) return toast('Ko‘pi bilan 7 ta oila a’zosi qo‘shiladi.', true);
    const number = $$('.family-member').length + 1;
    const node = document.createElement('div'); node.className = 'family-member';
    node.innerHTML = `<div class="family-member__number">${number}-oila a’zosi</div><button class="remove-member" type="button" aria-label="O‘chirish">×</button><div class="field"><label>To‘liq ism</label><input name="full_name" autocomplete="name" placeholder="Ism Familiya" required></div><div class="field"><label>Telefon raqami</label><input name="phone_number" type="tel" autocomplete="tel" placeholder="+998 90 123 45 67" required></div>`;
    $('.remove-member', node).addEventListener('click', () => { node.remove(); renumberMembers(); updateTotal(); });
    $('#familyMembers').append(node); updateTotal();
  }

  function renumberMembers() { $$('.family-member__number').forEach((node, index) => { node.textContent = `${index + 1}-oila a’zosi`; }); }

  async function createPurchase() {
    const button = $('#continueToPayment');
    const members = $$('.family-member').map(node => ({ full_name: $('[name="full_name"]', node).value.trim(), phone_number: $('[name="phone_number"]', node).value.trim() }));
    if (state.type === 'family' && !members.length) return toast('Kamida bitta oila a’zosini qo‘shing.', true);
    if (members.some(member => !member.full_name || !member.phone_number)) return toast('Oila a’zolari ma’lumotlarini to‘liq kiriting.', true);
    button.disabled = true;
    try {
      const data = await api('/telegram-app/api/purchases/', { method: 'POST', body: JSON.stringify({ course_id: state.course.id, purchase_type: state.type, members }) });
      state.purchase = data.purchase; state.purchases.unshift(data.purchase); renderHistory(); openContract(data.purchase);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function renderPayment() {
    const p = state.purchase;
    if (!p.contract_accepted) {
      openContract(p);
      return;
    }
    $('#paymentSummary').innerHTML = `<div class="summary-row"><span>Kurs</span><strong>${escapeHtml(p.course)}</strong></div><div class="summary-row"><span>Xarid turi</span><strong>${escapeHtml(p.purchase_type_label)}</strong></div><div class="summary-row"><span>Ishtirokchilar</span><strong>${p.participant_count} kishi</strong></div><div class="summary-row summary-total"><span>Jami</span><strong>${money(p.total_amount)}</strong></div>`;
    $('#contractVersionLabel').textContent = `Versiya ${p.contract_version}`;
    const demo = document.body.dataset.demo === '1';
    $('#paymentNote').textContent = demo ? 'Demo to‘lov: mablag‘ yechilmaydi.' : 'To‘lov Multicard sahifasida amalga oshiriladi. To‘lovdan keyin Telegram ilovasiga qayting.';
    $('#paymentStatus').textContent = p.invoice_state === 'uncertain' || p.invoice_state === 'creating'
      ? 'To‘lov holati aniqlanmoqda. Qayta to‘lamang; administrator bilan bog‘laning.'
      : p.payment_status_label;
    $('#payButton').disabled = ['creating', 'uncertain', 'revert'].includes(p.invoice_state) || p.payment_status === 'refunded';
    $('#checkPayment').hidden = demo || !p.invoice_state;
    $('#payButton').textContent = p.checkout_url ? 'To‘lov sahifasini ochish' : 'To‘lovni amalga oshirish';
  }

  async function acceptTerms() {
    if (state.legalReadOnly) return showView(state.legalReturnView, 1);
    const button = $('#acceptTerms'); button.disabled = true; button.textContent = 'Saqlanmoqda…';
    try {
      await api('/telegram-app/api/legal/terms/accept/', {
        method: 'POST',
        body: JSON.stringify({ accepted: true, version: state.legal.version }),
      });
      toast('Foydalanish shartlari qabul qilindi.');
      showView('homeView', 1);
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = 'Qabul qilaman';
    }
  }

  async function acceptContract() {
    if (state.legalReadOnly) return showView(state.legalReturnView, 1);
    const button = $('#acceptContract'); button.disabled = true; button.textContent = 'Saqlanmoqda…';
    try {
      const data = await api(`/telegram-app/api/purchases/${state.purchase.id}/contract/accept/`, {
        method: 'POST',
        body: JSON.stringify({ accepted: true, version: state.legal.version }),
      });
      state.purchase = data.purchase;
      replacePurchase(data.purchase);
      renderPayment();
      showView('paymentView', 2);
      toast('Shartnoma qabul qilindi. Endi to‘lovni amalga oshirishingiz mumkin.');
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = 'Shartnomani qabul qilaman';
    }
  }

  function applyPayment(purchase) {
    replacePurchase(purchase);
    if (state.purchase?.id !== purchase.id) return;
    state.purchase = purchase;
    if (purchase.payment_status === 'success') {
      if (!$('#paymentView').classList.contains('is-active')) return;
      if (purchase.questionnaire_completed) showView('successView', 3);
      else { renderQuestionnaires(); showView('questionnaireView', 3); }
      toast('To‘lov muvaffaqiyatli qabul qilindi.');
    } else { renderPayment(); }
  }

  async function startPayment() {
    const purchaseId = state.purchase.id;
    const button = $('#payButton'); button.disabled = true; button.textContent = 'To‘lov tayyorlanmoqda…';
    try {
      const endpoint = document.body.dataset.demo === '1' ? 'demo-payment' : 'payment';
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/${endpoint}/`, { method: 'POST', body: '{}' });
      applyPayment(data.purchase);
      if (data.purchase.payment_status !== 'success' && data.checkout_url) {
        const url = new URL(data.checkout_url);
        if (url.protocol !== 'https:') throw new Error('To‘lov havolasi noto‘g‘ri.');
        if (tg?.initData && tg.openLink) tg.openLink(url.href);
        else window.location.assign(url.href);
        $('#paymentStatus').textContent = 'To‘lov kutilmoqda. Yakunlagach, holatni tekshiring.';
      }
    } catch (error) { toast(error.message, true); }
    finally { if (state.purchase?.id === purchaseId) renderPayment(); }
  }

  let checkingPayment = false;
  async function checkPayment(remote = false) {
    if (checkingPayment || !state.purchase || !$('#paymentView').classList.contains('is-active')) return;
    checkingPayment = true;
    const purchaseId = state.purchase.id;
    $('#checkPayment').disabled = true;
    try {
      const path = remote ? 'check' : 'status';
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/payment/${path}/`, remote ? { method: 'POST', body: '{}' } : {});
      applyPayment(data.purchase);
      if (remote && data.purchase.payment_status !== 'success') toast(data.purchase.payment_status_label);
    } catch (error) { if (remote) toast(error.message, true); }
    finally { checkingPayment = false; $('#checkPayment').disabled = false; }
  }

  function replacePurchase(purchase) { const index = state.purchases.findIndex(item => item.id === purchase.id); if (index >= 0) state.purchases[index] = purchase; else state.purchases.unshift(purchase); renderHistory(); }

  function renderQuestionnaires() {
    $('#questionnaireMembers').innerHTML = state.purchase.members.map((member, index) => `
      <article class="questionnaire-card" data-member-id="${member.id}"><h3>${escapeHtml(member.full_name)}</h3><p>${index === 0 ? 'Xaridor' : 'Oila a’zosi'} · ${escapeHtml(member.phone_number)}</p>
        <div class="field"><label>Tug‘ilgan sana *</label><input type="date" name="birth_date" required></div>
        <div class="field"><label>Shahar / tuman *</label><input name="city" placeholder="Masalan: Toshkent, Chilonzor" required></div>
        <div class="field"><label>Kasb / faoliyat</label><input name="occupation" placeholder="Faoliyatingiz"></div>
        <div class="field"><label>Kursdan maqsadingiz *</label><textarea name="learning_goal" placeholder="Nimaga erishmoqchisiz?" required></textarea></div>
        <div class="field"><label>Oldingi tajriba</label><textarea name="prior_experience" placeholder="Shunga o‘xshash kurslarda qatnashganmisiz?"></textarea></div>
        <div class="field"><label>Muhim sog‘liq izohlari</label><textarea name="health_notes" placeholder="Bilishimiz kerak bo‘lgan ma’lumot (ixtiyoriy)"></textarea></div>
        <label class="consent"><input type="checkbox" name="consent" required><span>Ma’lumotlar to‘g‘ri ekanini tasdiqlayman va ulardan kursni tashkil etish uchun foydalanishga roziman.</span></label>
      </article>`).join('');
  }

  async function submitQuestionnaire(event) {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const button = $('button[type="submit"]', event.currentTarget); button.disabled = true;
    const responses = $$('.questionnaire-card').map(card => ({ member_id: Number(card.dataset.memberId), birth_date: $('[name="birth_date"]', card).value, city: $('[name="city"]', card).value.trim(), occupation: $('[name="occupation"]', card).value.trim(), learning_goal: $('[name="learning_goal"]', card).value.trim(), prior_experience: $('[name="prior_experience"]', card).value.trim(), health_notes: $('[name="health_notes"]', card).value.trim(), consent: $('[name="consent"]', card).checked }));
    try {
      const data = await api(`/telegram-app/api/purchases/${state.purchase.id}/questionnaire/`, { method: 'POST', body: JSON.stringify({ responses }) });
      state.purchase = data.purchase; replacePurchase(data.purchase); showView('successView', 3);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function resumePurchase(id) {
    state.purchase = state.purchases.find(item => item.id === id);
    if (state.purchase.payment_status === 'success') { renderQuestionnaires(); showView('questionnaireView', 3); }
    else if (!state.purchase.contract_accepted) { openContract(state.purchase); }
    else { renderPayment(); showView('paymentView', 2); }
  }

  function goHome() { showView('homeView', 1); renderHistory(); $$('.bottom-nav button').forEach((node, i) => node.classList.toggle('is-active', i === 0)); }

  async function bootstrap() {
    try {
      const data = await api('/telegram-app/api/bootstrap/'); Object.assign(state, { profile: data.profile, courses: data.courses, purchases: data.purchases });
      renderProfile(); renderCourses(); renderHistory();
      if (data.legal.terms_required) openTerms(true);
    } catch (error) {
      $('#courseList').innerHTML = `<article class="course-card"><h3>Ilovani ochib bo‘lmadi</h3><p>${escapeHtml(error.message)} Telegram bot ichidagi tugma orqali qayta urinib ko‘ring.</p></article>`;
      toast(error.message, true);
    }
  }

  $$('.segment__item').forEach(button => button.addEventListener('click', () => { state.type = button.dataset.purchaseType; $$('.segment__item').forEach(node => node.classList.toggle('is-active', node === button)); $('#familyArea').hidden = state.type !== 'family'; if (state.type === 'family' && !$$('.family-member').length) addFamilyMember(); updateTotal(); }));
  $('#addFamilyMember').addEventListener('click', addFamilyMember); $('#continueToPayment').addEventListener('click', createPurchase); $('#payButton').addEventListener('click', startPayment); $('#questionnaireForm').addEventListener('submit', submitQuestionnaire);
  $('#acceptTerms').addEventListener('click', acceptTerms); $('#acceptContract').addEventListener('click', acceptContract);
  $('#viewTerms').addEventListener('click', () => openTerms(false)); $('#viewContract').addEventListener('click', () => openContract(state.purchase, true));
  $('#contractBack').addEventListener('click', goHome); $('#checkoutBack').addEventListener('click', goHome); $('#paymentBack').addEventListener('click', () => state.purchase ? goHome() : showView('checkoutView', 1));
  $('#profileButton').addEventListener('click', () => showView('profileView', 1)); $$('[data-go-home]').forEach(node => node.addEventListener('click', goHome));
  $$('[data-nav]').forEach(button => button.addEventListener('click', () => { $$('.bottom-nav button').forEach(node => node.classList.toggle('is-active', node === button)); if (button.dataset.nav === 'profile') showView('profileView', 1); else { showView('homeView', 1); if (button.dataset.nav === 'courses') $('#courseList').scrollIntoView({ behavior: 'smooth' }); } }));
  $('#checkPayment').addEventListener('click', () => checkPayment(true));
  window.addEventListener('focus', () => checkPayment());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkPayment(); });
  setInterval(() => { if (!document.hidden) checkPayment(); }, 5000);
  bootstrap();
})();
